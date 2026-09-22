from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.capture import ConfirmCaptureDraft
from mintflow.config import Settings
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    Money,
    TransactionDate,
)
from mintflow.domain.user import User
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
    get_authenticate_web_session,
)
from mintflow.http.capture import (
    CaptureRuntime,
    get_capture_draft_repository,
    get_capture_runtime,
    get_confirm_capture_draft,
    get_expense_repository,
)
from mintflow.main import create_app

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
SESSION_SECRET = "A" * 43


class StubSessionAuthentication:
    def __init__(self, result: AuthenticatedWebSession | None) -> None:
        self.result = result

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        return self.result


class FakeCaptureDraftRepository:
    def __init__(self) -> None:
        self.store: dict[UUID, CaptureDraft] = {}
        self.create_calls: list[CaptureDraft] = []
        self.update_calls: list[CaptureDraft] = []

    def create(self, draft: CaptureDraft) -> None:
        self.store[draft.id] = draft
        self.create_calls.append(draft)

    def get(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None:
        draft = self.store.get(draft_id)
        if draft is None or draft.owner_id != owner_id:
            return None
        return draft

    def get_for_update(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None:
        return self.get(draft_id=draft_id, owner_id=owner_id)

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None:
        self.store[draft.id] = draft
        self.update_calls.append(draft)


class FakeExpenseRepository:
    def __init__(self) -> None:
        self.store: dict[UUID, Expense] = {}
        self.create_calls: list[Expense] = []

    def create(self, expense: Expense, *, commit: bool = True) -> None:
        self.store[expense.id] = expense
        self.create_calls.append(expense)

    def get(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        expense = self.store.get(expense_id)
        if expense is None or expense.owner_id != owner_id or not expense.is_active:
            return None
        return expense


class FakeUserRepository:
    def __init__(self, user: User) -> None:
        self.user = user

    def get(self, user_id: UUID) -> User | None:
        return self.user if user_id == self.user.id else None


class RecordingConfirmCaptureDraft:
    """A recording stand-in, per AUTH-13's CSRF-skip-the-use-case pattern."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def execute(self, *, draft_id: UUID, caller_id: UUID) -> Expense:
        self.calls.append((draft_id, caller_id))
        raise AssertionError("the use case must not be invoked")


def _app(
    settings: Settings, *, user_id: UUID | None = None
) -> tuple[FastAPI, FakeCaptureDraftRepository, UUID]:
    application = create_app(settings)
    owner_id = user_id if user_id is not None else uuid4()
    authenticated = AuthenticatedWebSession(session_id=uuid4(), user_id=owner_id)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(authenticated)
    )
    repository = FakeCaptureDraftRepository()
    application.dependency_overrides[get_capture_draft_repository] = lambda: repository
    application.dependency_overrides[get_capture_runtime] = lambda: CaptureRuntime(
        clock=lambda: NOW
    )
    return application, repository, owner_id


def _app_with_confirm(
    settings: Settings, *, user_id: UUID | None = None
) -> tuple[FastAPI, FakeCaptureDraftRepository, FakeExpenseRepository, UUID]:
    """Wire a real ConfirmCaptureDraft over fake repositories.

    Unlike _app(), this exercises the actual confirmation use case (success,
    idempotency, and rejection behavior), not just the HTTP boundary.
    """
    application, draft_repository, owner_id = _app(settings, user_id=user_id)

    user = replace(User.create(now=NOW), id=owner_id)
    expense_repository = FakeExpenseRepository()
    user_repository = FakeUserRepository(user)
    application.dependency_overrides[get_expense_repository] = lambda: expense_repository
    application.dependency_overrides[get_confirm_capture_draft] = lambda: ConfirmCaptureDraft(
        draft_repository=draft_repository,
        expense_repository=expense_repository,
        user_repository=user_repository,
        clock=lambda: NOW,
    )
    return application, draft_repository, expense_repository, owner_id


def _csrf_headers(application: FastAPI, settings: Settings) -> dict[str, str]:
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token = runtime.csrf_digester.derive(session_secret=SESSION_SECRET)
    return {CSRF_HEADER_NAME: token, "Origin": settings.authentication_web_origin}


def _cookies() -> dict[str, str]:
    return {AUTHENTICATED_SESSION_COOKIE_NAME: SESSION_SECRET}


@pytest.mark.anyio
async def test_full_happy_path_start_edit_edit_ready_view(settings: Settings) -> None:
    application, _repository, _owner_id = _app(settings)
    cookies = _cookies()
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        start_response = await client.post("/capture/drafts", cookies=cookies, headers=csrf_headers)
        assert start_response.status_code == 201
        draft_id = start_response.json()["id"]

        first_edit = await client.patch(
            f"/capture/drafts/{draft_id}",
            cookies=cookies,
            headers=csrf_headers,
            json={"amount_minor_units": 1500, "currency": "USD"},
        )
        assert first_edit.status_code == 200
        assert first_edit.json()["amount_minor_units"] == 1500
        assert first_edit.json()["revision"] == 1

        second_edit = await client.patch(
            f"/capture/drafts/{draft_id}",
            cookies=cookies,
            headers=csrf_headers,
            json={"transaction_date": "2026-08-06", "merchant": "Coffee Shop"},
        )
        assert second_edit.status_code == 200
        body = second_edit.json()
        assert body["transaction_date"] == "2026-08-06"
        assert body["merchant"] == "Coffee Shop"
        assert body["amount_minor_units"] == 1500
        assert body["revision"] == 3

        ready_response = await client.post(
            f"/capture/drafts/{draft_id}/ready", cookies=cookies, headers=csrf_headers
        )
        assert ready_response.status_code == 200
        assert ready_response.json()["state"] == "ready_for_review"

        view_response = await client.get(f"/capture/drafts/{draft_id}", cookies=cookies)
        assert view_response.status_code == 200
        final = view_response.json()
        assert final["state"] == "ready_for_review"
        assert final["amount_minor_units"] == 1500
        assert final["merchant"] == "Coffee Shop"
        assert final["amount_source"] == "user"


@pytest.mark.anyio
async def test_list_categories_requires_authentication(settings: Settings) -> None:
    application, _repository, _owner_id = _app(settings)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.get("/capture/categories")

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("POST", ""),
        ("GET", "/{draft_id}"),
        ("PATCH", "/{draft_id}"),
        ("POST", "/{draft_id}/ready"),
        ("POST", "/{draft_id}/cancel"),
    ],
)
async def test_every_route_requires_authentication(
    settings: Settings, method: str, path_suffix: str
) -> None:
    application, _repository, _owner_id = _app(settings)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )
    path = f"/capture/drafts{path_suffix.format(draft_id=uuid4())}"

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.request(method, path)

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("POST", ""),
        ("PATCH", "/{draft_id}"),
        ("POST", "/{draft_id}/ready"),
        ("POST", "/{draft_id}/cancel"),
    ],
)
async def test_every_unsafe_route_requires_csrf_and_leaves_draft_unchanged(
    settings: Settings, method: str, path_suffix: str
) -> None:
    application, repository, owner_id = _app(settings)
    existing = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    repository.store[existing.id] = existing
    path = f"/capture/drafts{path_suffix.format(draft_id=existing.id)}"

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.request(method, path, cookies=_cookies())

    assert response.status_code == 403
    assert response.json() == {"detail": "The request could not be verified."}
    assert repository.update_calls == []
    assert repository.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("GET", "/{draft_id}"),
        ("PATCH", "/{draft_id}"),
        ("POST", "/{draft_id}/ready"),
        ("POST", "/{draft_id}/cancel"),
    ],
)
async def test_cross_owner_access_returns_the_same_response_as_not_found(
    settings: Settings, method: str, path_suffix: str
) -> None:
    application, repository, _owner_id = _app(settings)
    other_owner_draft = CaptureDraft.start(
        owner_id=uuid4(), source=CaptureSource.WEB_MANUAL, now=NOW
    )
    repository.store[other_owner_draft.id] = other_owner_draft
    unknown_draft_id = uuid4()
    csrf_headers = _csrf_headers(application, settings)
    json_body = {"note": "hello"} if method == "PATCH" else None

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response_other_owner = await client.request(
            method,
            f"/capture/drafts{path_suffix.format(draft_id=other_owner_draft.id)}",
            cookies=_cookies(),
            headers=csrf_headers,
            json=json_body,
        )
        response_unknown = await client.request(
            method,
            f"/capture/drafts{path_suffix.format(draft_id=unknown_draft_id)}",
            cookies=_cookies(),
            headers=csrf_headers,
            json=json_body,
        )

    assert response_other_owner.status_code == response_unknown.status_code == 404
    assert response_other_owner.json() == response_unknown.json() == {"detail": "Draft not found."}
    assert repository.update_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("build", ["cancelled", "expired", "confirmed"])
@pytest.mark.parametrize(
    ("method", "path_suffix", "json_body"),
    [
        ("PATCH", "/{draft_id}", {"note": "late edit"}),
        ("POST", "/{draft_id}/ready", None),
        ("POST", "/{draft_id}/cancel", None),
    ],
)
async def test_mutating_a_terminal_draft_is_rejected(
    settings: Settings,
    method: str,
    path_suffix: str,
    json_body: dict[str, object] | None,
    build: str,
) -> None:
    application, repository, owner_id = _app(settings)
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    if build == "cancelled":
        draft = draft.cancel(caller_id=owner_id, now=NOW)
    elif build == "expired":
        draft = draft.expire(now=NOW)
    else:
        draft = draft.set_amount(
            caller_id=owner_id,
            amount=Money(minor_units=1000, currency=CurrencyCode("USD")),
            now=NOW,
        )
        draft = draft.set_transaction_date(
            caller_id=owner_id,
            transaction_date=TransactionDate(NOW.date()),
            now=NOW,
        )
        draft = draft.mark_ready_for_review(caller_id=owner_id, now=NOW)
        draft = draft.confirm(caller_id=owner_id, expense_id=uuid4(), now=NOW)
    repository.store[draft.id] = draft
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.request(
            method,
            f"/capture/drafts{path_suffix.format(draft_id=draft.id)}",
            cookies=_cookies(),
            headers=csrf_headers,
            json=json_body,
        )

    assert response.status_code == 409
    assert repository.update_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "json_body",
    [
        {"amount_minor_units": 1000},
        {"unexpected_field": True},
        {"note": "x" * 2001},
    ],
)
async def test_malformed_edit_bodies_return_422(
    settings: Settings, json_body: dict[str, object]
) -> None:
    application, repository, owner_id = _app(settings)
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    repository.store[draft.id] = draft
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.patch(
            f"/capture/drafts/{draft.id}",
            cookies=_cookies(),
            headers=csrf_headers,
            json=json_body,
        )

    assert response.status_code == 422
    assert repository.update_calls == []


def _confirmable_draft(owner_id: UUID) -> CaptureDraft:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    draft = draft.set_amount(
        caller_id=owner_id,
        amount=Money(minor_units=1500, currency=CurrencyCode("USD")),
        now=NOW,
    )
    draft = draft.set_transaction_date(
        caller_id=owner_id, transaction_date=TransactionDate(NOW.date()), now=NOW
    )
    return draft.mark_ready_for_review(caller_id=owner_id, now=NOW)


@pytest.mark.anyio
async def test_confirm_returns_the_expense_with_the_documented_response_shape(
    settings: Settings,
) -> None:
    application, draft_repository, _expense_repository, owner_id = _app_with_confirm(settings)
    draft = _confirmable_draft(owner_id)
    draft_repository.store[draft.id] = draft
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.post(
            f"/capture/drafts/{draft.id}/confirm", cookies=_cookies(), headers=csrf_headers
        )

    assert response.status_code == 200
    body = response.json()
    assert body["amount_minor_units"] == 1500
    assert body["currency"] == "USD"
    assert body["capture_draft_id"] == str(draft.id)
    assert body["category_key"] == "uncategorized"
    assert draft_repository.store[draft.id].state.value == "confirmed"


@pytest.mark.anyio
async def test_duplicate_confirm_returns_the_same_expense(settings: Settings) -> None:
    application, draft_repository, expense_repository, owner_id = _app_with_confirm(settings)
    draft = _confirmable_draft(owner_id)
    draft_repository.store[draft.id] = draft
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        first = await client.post(
            f"/capture/drafts/{draft.id}/confirm", cookies=_cookies(), headers=csrf_headers
        )
        second = await client.post(
            f"/capture/drafts/{draft.id}/confirm", cookies=_cookies(), headers=csrf_headers
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(expense_repository.store) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("build", ["not_confirmable", "cancelled", "expired", "future_dated"])
async def test_confirm_rejects_drafts_that_cannot_be_confirmed(
    settings: Settings, build: str
) -> None:
    application, draft_repository, expense_repository, owner_id = _app_with_confirm(settings)
    if build == "not_confirmable":
        draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    elif build == "cancelled":
        draft = _confirmable_draft(owner_id).cancel(caller_id=owner_id, now=NOW)
    elif build == "expired":
        draft = _confirmable_draft(owner_id).expire(now=NOW)
    else:
        draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
        draft = draft.set_amount(
            caller_id=owner_id,
            amount=Money(minor_units=1000, currency=CurrencyCode("USD")),
            now=NOW,
        )
        draft = draft.set_transaction_date(
            caller_id=owner_id,
            transaction_date=TransactionDate(NOW.date() + timedelta(days=5)),
            now=NOW,
        )
        draft = draft.mark_ready_for_review(caller_id=owner_id, now=NOW)
    draft_repository.store[draft.id] = draft
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.post(
            f"/capture/drafts/{draft.id}/confirm", cookies=_cookies(), headers=csrf_headers
        )

    assert response.status_code == 409
    assert expense_repository.store == {}


@pytest.mark.anyio
async def test_confirm_csrf_failure_leaves_state_unchanged_and_never_invokes_use_case(
    settings: Settings,
) -> None:
    application, draft_repository, _expense_repository, owner_id = _app_with_confirm(settings)
    draft = _confirmable_draft(owner_id)
    draft_repository.store[draft.id] = draft
    recorder = RecordingConfirmCaptureDraft()
    application.dependency_overrides[get_confirm_capture_draft] = lambda: recorder

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.post(f"/capture/drafts/{draft.id}/confirm", cookies=_cookies())

    assert response.status_code == 403
    assert recorder.calls == []
    assert draft_repository.store[draft.id].state.value == "ready_for_review"


@pytest.mark.anyio
async def test_confirm_and_view_expense_require_authentication(settings: Settings) -> None:
    application, _draft_repository, _expense_repository, owner_id = _app_with_confirm(settings)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        confirm_response = await client.post(f"/capture/drafts/{uuid4()}/confirm")
        view_response = await client.get(f"/capture/expenses/{uuid4()}")

    assert confirm_response.status_code == view_response.status_code == 401


@pytest.mark.anyio
async def test_cross_owner_confirm_and_view_match_not_found(settings: Settings) -> None:
    application, draft_repository, expense_repository, owner_id = _app_with_confirm(settings)
    other_owner_draft = _confirmable_draft(uuid4())
    draft_repository.store[other_owner_draft.id] = other_owner_draft
    other_owner_expense = Expense.create(
        owner_id=uuid4(),
        money=Money(minor_units=1000, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(NOW.date()),
        category_key="uncategorized",
        capture_draft_id=uuid4(),
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )
    expense_repository.store[other_owner_expense.id] = other_owner_expense
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        confirm_response = await client.post(
            f"/capture/drafts/{other_owner_draft.id}/confirm",
            cookies=_cookies(),
            headers=csrf_headers,
        )
        view_response = await client.get(
            f"/capture/expenses/{other_owner_expense.id}", cookies=_cookies()
        )
        unknown_view_response = await client.get(f"/capture/expenses/{uuid4()}", cookies=_cookies())

    assert confirm_response.status_code == 404
    assert view_response.status_code == unknown_view_response.status_code == 404
    assert view_response.json() == unknown_view_response.json()
