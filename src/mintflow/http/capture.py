from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.http.authentication import (
    AuthenticatedPrincipal,
    AuthenticatedPrincipalDependency,
    CsrfProtectedPrincipalDependency,
    DatabaseSession,
    authentication_security_headers,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyCategoryRepository,
)

MAX_CAPTURE_REQUEST_BODY_BYTES = 4_096
MAX_NOTE_LENGTH = 2_000
GENERIC_DRAFT_NOT_FOUND_MESSAGE = "Draft not found."
GENERIC_DRAFT_REJECTED_MESSAGE = "The request could not be applied."
GENERIC_MALFORMED_BODY_MESSAGE = "The request body is invalid."

router = APIRouter(prefix="/capture", tags=["capture"])


@dataclass(frozen=True, slots=True)
class CaptureRuntime:
    clock: Callable[[], datetime]


def build_capture_runtime(
    *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> CaptureRuntime:
    return CaptureRuntime(clock=clock)


async def get_capture_runtime(request: Request) -> CaptureRuntime:
    runtime: CaptureRuntime = request.app.state.capture_runtime
    return runtime


CaptureRuntimeDependency = Annotated[CaptureRuntime, Depends(get_capture_runtime)]


async def get_capture_draft_repository(
    session: DatabaseSession,
) -> SqlAlchemyCaptureDraftRepository:
    return SqlAlchemyCaptureDraftRepository(session)


CaptureDraftRepositoryDependency = Annotated[
    SqlAlchemyCaptureDraftRepository, Depends(get_capture_draft_repository)
]


async def get_category_repository(session: DatabaseSession) -> SqlAlchemyCategoryRepository:
    return SqlAlchemyCategoryRepository(session)


CategoryRepositoryDependency = Annotated[
    SqlAlchemyCategoryRepository, Depends(get_category_repository)
]


class EditCaptureDraftRequest(BaseModel):
    """All fields optional: a client sends only what changed."""

    model_config = ConfigDict(extra="forbid")

    amount_minor_units: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    transaction_date: date | None = None
    merchant: str | None = Field(default=None, min_length=1, max_length=MerchantName.MAX_LENGTH)
    category_key: str | None = Field(default=None, min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @model_validator(mode="after")
    def _amount_and_currency_together(self) -> "EditCaptureDraftRequest":
        if (self.amount_minor_units is None) != (self.currency is None):
            raise ValueError("amount_minor_units and currency must be provided together")
        return self


class CategoryResponse(BaseModel):
    key: str
    name: str


class CaptureDraftResponse(BaseModel):
    id: UUID
    state: str
    revision: int
    amount_minor_units: int | None
    currency: str | None
    amount_source: str | None
    transaction_date: date | None
    transaction_date_source: str | None
    merchant: str | None
    merchant_source: str | None
    category_key: str | None
    category_key_source: str | None
    note: str | None
    expense_id: UUID | None
    created_at: datetime
    modified_at: datetime
    confirmed_at: datetime | None


def _to_response(draft: CaptureDraft) -> CaptureDraftResponse:
    return CaptureDraftResponse(
        id=draft.id,
        state=draft.state.value,
        revision=draft.revision,
        amount_minor_units=draft.amount.minor_units if draft.amount is not None else None,
        currency=draft.amount.currency.value if draft.amount is not None else None,
        amount_source=draft.amount_source.value if draft.amount_source is not None else None,
        transaction_date=(
            draft.transaction_date.value if draft.transaction_date is not None else None
        ),
        transaction_date_source=(
            draft.transaction_date_source.value
            if draft.transaction_date_source is not None
            else None
        ),
        merchant=draft.merchant.value if draft.merchant is not None else None,
        merchant_source=(
            draft.merchant_source.value if draft.merchant_source is not None else None
        ),
        category_key=draft.category_key,
        category_key_source=(
            draft.category_key_source.value if draft.category_key_source is not None else None
        ),
        note=draft.note,
        expense_id=draft.expense_id,
        created_at=draft.created_at,
        modified_at=draft.modified_at,
        confirmed_at=draft.confirmed_at,
    )


def _draft_response(draft: CaptureDraft, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        _to_response(draft).model_dump(mode="json"),
        status_code=status_code,
        headers=authentication_security_headers(),
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=GENERIC_DRAFT_NOT_FOUND_MESSAGE,
        headers=authentication_security_headers(),
    )


def _rejected() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=GENERIC_DRAFT_REJECTED_MESSAGE,
        headers=authentication_security_headers(),
    )


def _malformed() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=GENERIC_MALFORMED_BODY_MESSAGE,
        headers=authentication_security_headers(),
    )


async def _read_bounded_body(request: Request) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CAPTURE_REQUEST_BODY_BYTES:
            return None
        body.extend(chunk)
    return bytes(body)


async def parse_edit_capture_draft_request(request: Request) -> EditCaptureDraftRequest | None:
    body = await _read_bounded_body(request)
    if body is None:
        return None
    try:
        return EditCaptureDraftRequest.model_validate_json(body)
    except ValidationError:
        return None


def _get_owned_draft(
    repository: SqlAlchemyCaptureDraftRepository,
    *,
    draft_id: UUID,
    principal: AuthenticatedPrincipal,
) -> CaptureDraft:
    draft = repository.get(draft_id=draft_id, owner_id=principal.user_id)
    if draft is None:
        raise _not_found()
    return draft


@router.post("/drafts", status_code=201)
async def start_draft(
    principal: CsrfProtectedPrincipalDependency,
    repository: CaptureDraftRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> JSONResponse:
    draft = CaptureDraft.start(
        owner_id=principal.user_id, source=CaptureSource.WEB_MANUAL, now=runtime.clock()
    )
    repository.create(draft)
    return _draft_response(draft, status_code=201)


@router.get("/drafts/{draft_id}")
async def view_draft(
    draft_id: UUID,
    principal: AuthenticatedPrincipalDependency,
    repository: CaptureDraftRepositoryDependency,
) -> JSONResponse:
    draft = _get_owned_draft(repository, draft_id=draft_id, principal=principal)
    return _draft_response(draft)


@router.patch("/drafts/{draft_id}")
async def edit_draft(
    draft_id: UUID,
    request: Request,
    principal: CsrfProtectedPrincipalDependency,
    repository: CaptureDraftRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> JSONResponse:
    edit_request = await parse_edit_capture_draft_request(request)
    if edit_request is None:
        raise _malformed()

    draft = _get_owned_draft(repository, draft_id=draft_id, principal=principal)
    now = runtime.clock()
    try:
        if edit_request.amount_minor_units is not None and edit_request.currency is not None:
            money = Money(
                minor_units=edit_request.amount_minor_units,
                currency=CurrencyCode(edit_request.currency),
            )
            draft = draft.set_amount(caller_id=principal.user_id, amount=money, now=now)
        if edit_request.transaction_date is not None:
            draft = draft.set_transaction_date(
                caller_id=principal.user_id,
                transaction_date=TransactionDate(edit_request.transaction_date),
                now=now,
            )
        if edit_request.merchant is not None:
            draft = draft.set_merchant(
                caller_id=principal.user_id,
                merchant=MerchantName(edit_request.merchant),
                now=now,
            )
        if edit_request.category_key is not None:
            draft = draft.set_category_key(
                caller_id=principal.user_id,
                category_key=edit_request.category_key,
                now=now,
            )
        if edit_request.note is not None:
            draft = draft.set_note(caller_id=principal.user_id, note=edit_request.note, now=now)
    except ValueError:
        raise _rejected() from None

    repository.update(draft)
    return _draft_response(draft)


@router.post("/drafts/{draft_id}/ready")
async def mark_draft_ready_for_review(
    draft_id: UUID,
    principal: CsrfProtectedPrincipalDependency,
    repository: CaptureDraftRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> JSONResponse:
    draft = _get_owned_draft(repository, draft_id=draft_id, principal=principal)
    try:
        draft = draft.mark_ready_for_review(caller_id=principal.user_id, now=runtime.clock())
    except ValueError:
        raise _rejected() from None
    repository.update(draft)
    return _draft_response(draft)


@router.post("/drafts/{draft_id}/cancel")
async def cancel_draft(
    draft_id: UUID,
    principal: CsrfProtectedPrincipalDependency,
    repository: CaptureDraftRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> JSONResponse:
    draft = _get_owned_draft(repository, draft_id=draft_id, principal=principal)
    try:
        draft = draft.cancel(caller_id=principal.user_id, now=runtime.clock())
    except ValueError:
        raise _rejected() from None
    repository.update(draft)
    return _draft_response(draft)


@router.get("/categories")
async def list_categories(
    principal: AuthenticatedPrincipalDependency,
    category_repository: CategoryRepositoryDependency,
) -> JSONResponse:
    """List active system categories, for a client to offer as edit choices.

    Read-only, authenticated only (no CSRF/ownership scoping): categories
    are system-wide, not owned by any User.
    """
    categories = category_repository.list_active()
    return JSONResponse(
        [
            CategoryResponse(key=category.key, name=category.name).model_dump()
            for category in categories
        ],
        headers=authentication_security_headers(),
    )
