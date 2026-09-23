from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mintflow.application.capture import (
    UNCHANGED,
    CaptureDraftAccessDenied,
    CaptureDraftNotConfirmable,
    ConfirmCaptureDraft,
    DeleteExpense,
    EditExpense,
    ExpenseEdit,
    ExpenseEditRejected,
    ExpenseNotFound,
    RestoreExpense,
    Unchanged,
)
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
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
    SqlAlchemyExpenseChangeRecordAppender,
    SqlAlchemyExpenseRepository,
    SqlAlchemyUserRepository,
)

MAX_CAPTURE_REQUEST_BODY_BYTES = 4_096
MAX_NOTE_LENGTH = 2_000
GENERIC_DRAFT_NOT_FOUND_MESSAGE = "Draft not found."
GENERIC_DRAFT_REJECTED_MESSAGE = "The request could not be applied."
GENERIC_MALFORMED_BODY_MESSAGE = "The request body is invalid."
GENERIC_EXPENSE_NOT_FOUND_MESSAGE = "Expense not found."

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


async def get_expense_repository(session: DatabaseSession) -> SqlAlchemyExpenseRepository:
    return SqlAlchemyExpenseRepository(session)


ExpenseRepositoryDependency = Annotated[
    SqlAlchemyExpenseRepository, Depends(get_expense_repository)
]


async def get_user_repository(session: DatabaseSession) -> SqlAlchemyUserRepository:
    return SqlAlchemyUserRepository(session)


UserRepositoryDependency = Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)]


async def get_confirm_capture_draft(
    draft_repository: CaptureDraftRepositoryDependency,
    expense_repository: ExpenseRepositoryDependency,
    user_repository: UserRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> ConfirmCaptureDraft:
    return ConfirmCaptureDraft(
        draft_repository=draft_repository,
        expense_repository=expense_repository,
        user_repository=user_repository,
        clock=runtime.clock,
    )


ConfirmCaptureDraftDependency = Annotated[ConfirmCaptureDraft, Depends(get_confirm_capture_draft)]


async def get_edit_expense(
    session: DatabaseSession,
    expense_repository: ExpenseRepositoryDependency,
    category_repository: CategoryRepositoryDependency,
    user_repository: UserRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> EditExpense:
    return EditExpense(
        expense_repository=expense_repository,
        change_records=SqlAlchemyExpenseChangeRecordAppender(session),
        category_repository=category_repository,
        user_repository=user_repository,
        clock=runtime.clock,
    )


EditExpenseDependency = Annotated[EditExpense, Depends(get_edit_expense)]


async def get_delete_expense(
    session: DatabaseSession,
    expense_repository: ExpenseRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> DeleteExpense:
    return DeleteExpense(
        expense_repository=expense_repository,
        change_records=SqlAlchemyExpenseChangeRecordAppender(session),
        clock=runtime.clock,
    )


DeleteExpenseDependency = Annotated[DeleteExpense, Depends(get_delete_expense)]


async def get_restore_expense(
    session: DatabaseSession,
    expense_repository: ExpenseRepositoryDependency,
    runtime: CaptureRuntimeDependency,
) -> RestoreExpense:
    return RestoreExpense(
        expense_repository=expense_repository,
        change_records=SqlAlchemyExpenseChangeRecordAppender(session),
        clock=runtime.clock,
    )


RestoreExpenseDependency = Annotated[RestoreExpense, Depends(get_restore_expense)]


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


_CLEARABLE_EXPENSE_FIELDS = frozenset({"merchant", "note"})


class EditExpenseRequest(BaseModel):
    """Partial update of a confirmed Expense (design D4).

    An absent field is unchanged. Explicit ``null`` clears ``merchant`` or
    ``note`` and is rejected for every other field. Presence is read from
    ``model_fields_set``, which is what distinguishes absent from ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    amount_minor_units: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    transaction_date: date | None = None
    merchant: str | None = Field(default=None, min_length=1, max_length=MerchantName.MAX_LENGTH)
    category_key: str | None = Field(default=None, min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @model_validator(mode="after")
    def _presence_rules(self) -> "EditExpenseRequest":
        supplied = self.model_fields_set
        for name in supplied - _CLEARABLE_EXPENSE_FIELDS:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if ("amount_minor_units" in supplied) != ("currency" in supplied):
            raise ValueError("amount_minor_units and currency must be provided together")
        return self

    def to_edit(self) -> ExpenseEdit:
        """Build the command from domain values; raises ValueError on an invalid value.

        After ``_presence_rules``, a required field is non-null exactly when
        it was supplied, so ``None`` there means "unchanged".
        """
        supplied = self.model_fields_set
        money: Money | Unchanged = UNCHANGED
        if self.amount_minor_units is not None and self.currency is not None:
            money = Money(minor_units=self.amount_minor_units, currency=CurrencyCode(self.currency))
        merchant: MerchantName | None | Unchanged = UNCHANGED
        if "merchant" in supplied:
            merchant = MerchantName(self.merchant) if self.merchant is not None else None
        return ExpenseEdit(
            money=money,
            transaction_date=(
                TransactionDate(self.transaction_date)
                if self.transaction_date is not None
                else UNCHANGED
            ),
            merchant=merchant,
            category_key=self.category_key if self.category_key is not None else UNCHANGED,
            note=self.note if "note" in supplied else UNCHANGED,
        )


class CategoryResponse(BaseModel):
    key: str
    name: str


class ExpenseResponse(BaseModel):
    id: UUID
    amount_minor_units: int
    currency: str
    transaction_date: date
    merchant: str | None
    category_key: str
    note: str | None
    source: str
    capture_draft_id: UUID
    created_at: datetime
    modified_at: datetime


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


def _to_expense_response(expense: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=expense.id,
        amount_minor_units=expense.money.minor_units,
        currency=expense.money.currency.value,
        transaction_date=expense.transaction_date.value,
        merchant=expense.merchant.value if expense.merchant is not None else None,
        category_key=expense.category_key,
        note=expense.note,
        source=expense.source.value,
        capture_draft_id=expense.capture_draft_id,
        created_at=expense.created_at,
        modified_at=expense.modified_at,
    )


def _expense_response(expense: Expense) -> JSONResponse:
    return JSONResponse(
        _to_expense_response(expense).model_dump(mode="json"),
        headers=authentication_security_headers(),
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=GENERIC_DRAFT_NOT_FOUND_MESSAGE,
        headers=authentication_security_headers(),
    )


def _expense_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=GENERIC_EXPENSE_NOT_FOUND_MESSAGE,
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


async def parse_edit_expense_request(request: Request) -> EditExpenseRequest | None:
    body = await _read_bounded_body(request)
    if body is None:
        return None
    try:
        return EditExpenseRequest.model_validate_json(body)
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


@router.post("/drafts/{draft_id}/confirm")
async def confirm_draft(
    draft_id: UUID,
    principal: CsrfProtectedPrincipalDependency,
    confirm_use_case: ConfirmCaptureDraftDependency,
) -> JSONResponse:
    """Convert one CaptureDraft into exactly one Expense.

    The sprint's most safety-critical HTTP surface: the only route that can
    create financial history. A duplicate/repeated confirmation of the same
    draft returns 200 with the same Expense (CAPTURE-08's idempotent
    behavior), not an error.
    """
    try:
        expense = confirm_use_case.execute(draft_id=draft_id, caller_id=principal.user_id)
    except CaptureDraftAccessDenied:
        raise _not_found() from None
    except CaptureDraftNotConfirmable:
        raise _rejected() from None
    return _expense_response(expense)


@router.get("/expenses/{expense_id}")
async def view_expense(
    expense_id: UUID,
    principal: AuthenticatedPrincipalDependency,
    expense_repository: ExpenseRepositoryDependency,
) -> JSONResponse:
    expense = expense_repository.get_active(expense_id=expense_id, owner_id=principal.user_id)
    if expense is None:
        raise _expense_not_found()
    return _expense_response(expense)


@router.patch("/expenses/{expense_id}")
async def edit_expense(
    expense_id: UUID,
    request: Request,
    principal: CsrfProtectedPrincipalDependency,
    edit_use_case: EditExpenseDependency,
) -> JSONResponse:
    """Correct a confirmed Expense, mirroring the draft PATCH's status codes.

    A malformed body is 422; a well-formed but unacceptable value is 409.
    Missing, foreign, and soft-deleted Expenses share the view route's 404.
    """
    edit_request = await parse_edit_expense_request(request)
    if edit_request is None:
        raise _malformed()
    try:
        edit = edit_request.to_edit()
    except ValueError:
        raise _rejected() from None
    try:
        expense = edit_use_case.execute(
            expense_id=expense_id, caller_id=principal.user_id, edit=edit
        )
    except ExpenseNotFound:
        raise _expense_not_found() from None
    except ExpenseEditRejected:
        raise _rejected() from None
    return _expense_response(expense)


@router.delete("/expenses/{expense_id}", status_code=204)
async def delete_expense(
    expense_id: UUID,
    principal: CsrfProtectedPrincipalDependency,
    delete_use_case: DeleteExpenseDependency,
) -> Response:
    """Soft-delete an Expense. Deleting an already deleted Expense is also 204."""
    try:
        delete_use_case.execute(expense_id=expense_id, caller_id=principal.user_id)
    except ExpenseNotFound:
        raise _expense_not_found() from None
    return Response(status_code=204, headers=authentication_security_headers())


@router.post("/expenses/{expense_id}/restore")
async def restore_expense(
    expense_id: UUID,
    principal: CsrfProtectedPrincipalDependency,
    restore_use_case: RestoreExpenseDependency,
) -> JSONResponse:
    """Return a soft-deleted Expense to history. Restoring an active Expense is also 200."""
    try:
        expense = restore_use_case.execute(expense_id=expense_id, caller_id=principal.user_id)
    except ExpenseNotFound:
        raise _expense_not_found() from None
    return _expense_response(expense)
