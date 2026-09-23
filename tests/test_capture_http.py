from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.capture import (
    ConfirmCaptureDraft,
    DeleteExpense,
    EditExpense,
    ExpenseChangeRecord,
    ExpenseEdit,
    ExpenseHistoryFilter,
    ExpenseHistoryPage,
    ExpenseHistoryPosition,
    RestoreExpense,
    encode_history_cursor,
)
from mintflow.config import Settings
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    Category,
    CurrencyCode,
    Expense,
    MerchantName,
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
    get_delete_expense,
    get_edit_expense,
    get_expense_repository,
    get_restore_expense,
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
        if expense is None or expense.owner_id != owner_id:
            return None
        return expense

    def get_active(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        expense = self.get(expense_id=expense_id, owner_id=owner_id)
        return expense if expense is not None and expense.is_active else None

    def get_active_for_update(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        return self.get_active(expense_id=expense_id, owner_id=owner_id)

    def get_owned_for_update(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        return self.get(expense_id=expense_id, owner_id=owner_id)

    def list_history(
        self,
        *,
        owner_id: UUID,
        history_filter: ExpenseHistoryFilter,
        limit: int,
        after: ExpenseHistoryPosition | None = None,
    ) -> ExpenseHistoryPage:
        """An in-memory stand-in; the real query is covered against PostgreSQL."""

        def key(expense: Expense) -> tuple[object, ...]:
            return (expense.transaction_date.value, expense.created_at, expense.id)

        matching = sorted(
            (
                expense
                for expense in self.store.values()
                if expense.owner_id == owner_id
                and expense.is_active
                and (
                    not history_filter.category_keys
                    or expense.category_key in history_filter.category_keys
                )
                and (
                    after is None
                    or key(expense) < (after.transaction_date, after.created_at, after.expense_id)
                )
            ),
            key=key,
            reverse=True,
        )
        items = tuple(matching[:limit])
        next_position = ExpenseHistoryPosition.after(items[-1]) if len(matching) > limit else None
        return ExpenseHistoryPage(items=items, next_position=next_position)

    def update(self, expense: Expense, *, commit: bool = True) -> None:
        self.store[expense.id] = expense


class FakeCategoryRepository:
    def get_by_key(self, key: str) -> Category | None:
        if key not in {"groceries", "health", "uncategorized"}:
            return None
        return Category(id=uuid4(), key=key, name=key.title(), is_active=True)


class FakeChangeRecords:
    def __init__(self) -> None:
        self.records: list[ExpenseChangeRecord] = []

    def append(self, record: ExpenseChangeRecord) -> None:
        self.records.append(record)


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


class RecordingDeletionStateChange:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def execute(self, *, expense_id: UUID, caller_id: UUID) -> Expense:
        self.calls.append((expense_id, caller_id))
        raise AssertionError("the use case must not be invoked")


class RecordingEditExpense:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, ExpenseEdit]] = []

    def execute(self, *, expense_id: UUID, caller_id: UUID, edit: ExpenseEdit) -> Expense:
        self.calls.append((expense_id, caller_id, edit))
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


def _app_with_edit(
    settings: Settings,
) -> tuple[FastAPI, FakeExpenseRepository, FakeChangeRecords, Expense]:
    """Wire a real EditExpense over fakes, with one Expense owned by the caller."""
    application, _draft_repository, expense_repository, owner_id = _app_with_confirm(settings)
    user = replace(User.create(now=NOW), id=owner_id)
    change_records = FakeChangeRecords()
    application.dependency_overrides[get_delete_expense] = lambda: DeleteExpense(
        expense_repository=expense_repository,
        change_records=change_records,
        clock=lambda: NOW + timedelta(minutes=5),
    )
    application.dependency_overrides[get_restore_expense] = lambda: RestoreExpense(
        expense_repository=expense_repository,
        change_records=change_records,
        clock=lambda: NOW + timedelta(minutes=10),
    )
    application.dependency_overrides[get_edit_expense] = lambda: EditExpense(
        expense_repository=expense_repository,
        change_records=change_records,
        category_repository=FakeCategoryRepository(),
        user_repository=FakeUserRepository(user),
        clock=lambda: NOW + timedelta(minutes=5),
    )
    expense = Expense.create(
        owner_id=owner_id,
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(NOW.date()),
        category_key="groceries",
        capture_draft_id=uuid4(),
        merchant=MerchantName("Corner Shop"),
        note="milk",
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )
    expense_repository.store[expense.id] = expense
    return application, expense_repository, change_records, expense


@pytest.mark.anyio
async def test_edit_expense_returns_the_updated_expense_and_view_agrees(
    settings: Settings,
) -> None:
    application, _expenses, change_records, expense = _app_with_edit(settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.patch(
            f"/capture/expenses/{expense.id}",
            cookies=_cookies(),
            headers=_csrf_headers(application, settings),
            json={
                "amount_minor_units": 1750,
                "currency": "EUR",
                "category_key": "health",
                "merchant": None,
            },
        )
        view = await client.get(f"/capture/expenses/{expense.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["amount_minor_units"] == 1750
    assert body["currency"] == "EUR"
    assert body["category_key"] == "health"
    assert body["merchant"] is None
    assert body["note"] == "milk"  # absent: unchanged
    assert body["transaction_date"] == NOW.date().isoformat()
    assert body["modified_at"] == (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert response.headers["cache-control"] == "no-store"
    assert view.json() == body
    assert len(change_records.records) == 1


@pytest.mark.anyio
async def test_edit_expense_with_no_changes_returns_the_expense_unchanged(
    settings: Settings,
) -> None:
    application, expenses, change_records, expense = _app_with_edit(settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.patch(
            f"/capture/expenses/{expense.id}",
            cookies=_cookies(),
            headers=_csrf_headers(application, settings),
            json={"note": "milk"},
        )

    assert response.status_code == 200
    assert response.json()["modified_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert expenses.store[expense.id] == expense
    assert change_records.records == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "status_code"),
    [
        pytest.param({"note": None, "extra": 1}, 422, id="unknown field"),
        pytest.param({"category_key": None}, 422, id="null required field"),
        pytest.param({"amount_minor_units": 100}, 422, id="amount without currency"),
        pytest.param({"note": "x" * 5000}, 422, id="oversized body"),
        pytest.param({"amount_minor_units": 0, "currency": "USD"}, 409, id="zero amount"),
        pytest.param({"amount_minor_units": 100, "currency": "ZZZ"}, 409, id="bad currency"),
        pytest.param({"merchant": "   "}, 409, id="blank merchant"),
        pytest.param({"category_key": "not_a_category"}, 409, id="unknown category"),
        pytest.param({"transaction_date": "2026-08-20"}, 409, id="future date"),
    ],
)
async def test_edit_expense_rejects_invalid_input_without_changing_or_echoing_it(
    settings: Settings, body: dict[str, object], status_code: int
) -> None:
    application, expenses, change_records, expense = _app_with_edit(settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.patch(
            f"/capture/expenses/{expense.id}",
            cookies=_cookies(),
            headers=_csrf_headers(application, settings),
            json=body,
        )

    assert response.status_code == status_code
    assert set(response.json()) == {"detail"}
    for value in body.values():
        if isinstance(value, str) and value.strip():
            assert value not in response.text
    assert expenses.store[expense.id] == expense
    assert change_records.records == []


@pytest.mark.anyio
async def test_edit_expense_csrf_failure_leaves_state_unchanged_and_never_invokes_use_case(
    settings: Settings,
) -> None:
    application, expenses, _change_records, expense = _app_with_edit(settings)
    recorder = RecordingEditExpense()
    application.dependency_overrides[get_edit_expense] = lambda: recorder
    valid = _csrf_headers(application, settings)
    invalid_cases = [
        {},
        {"Origin": valid["Origin"]},
        {**valid, CSRF_HEADER_NAME: "wrong-token"},
        {**valid, "Origin": "https://attacker.test"},
    ]

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        responses = [
            await client.patch(
                f"/capture/expenses/{expense.id}",
                cookies=_cookies(),
                headers=headers,
                json={"note": "changed"},
            )
            for headers in invalid_cases
        ]

    assert [response.status_code for response in responses] == [403] * len(invalid_cases)
    assert recorder.calls == []
    assert expenses.store[expense.id] == expense


@pytest.mark.anyio
async def test_edit_expense_foreign_deleted_and_unknown_match_not_found(
    settings: Settings,
) -> None:
    application, expenses, change_records, expense = _app_with_edit(settings)
    foreign = replace(expense, id=uuid4(), owner_id=uuid4())
    deleted = replace(expense, id=uuid4(), capture_draft_id=uuid4()).delete(now=NOW)
    expenses.store[foreign.id] = foreign
    expenses.store[deleted.id] = deleted
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        responses = [
            await client.patch(
                f"/capture/expenses/{expense_id}",
                cookies=_cookies(),
                headers=csrf_headers,
                json={"note": "changed"},
            )
            for expense_id in (foreign.id, deleted.id, uuid4())
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert responses[0].json() == responses[1].json() == responses[2].json()
    assert expenses.store[foreign.id] == foreign
    assert expenses.store[deleted.id] == deleted
    assert change_records.records == []


@pytest.mark.anyio
async def test_edit_expense_requires_authentication(settings: Settings) -> None:
    application, _expenses, _change_records, expense = _app_with_edit(settings)
    recorder = RecordingEditExpense()
    application.dependency_overrides[get_edit_expense] = lambda: recorder
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.patch(
            f"/capture/expenses/{expense.id}",
            cookies=_cookies(),
            headers=_csrf_headers(application, settings),
            json={"note": "changed"},
        )

    assert response.status_code == 401
    assert recorder.calls == []


@pytest.mark.anyio
async def test_delete_hides_the_expense_and_restore_brings_it_back(settings: Settings) -> None:
    application, expenses, change_records, expense = _app_with_edit(settings)
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        deleted = await client.delete(
            f"/capture/expenses/{expense.id}", cookies=_cookies(), headers=csrf_headers
        )
        deleted_again = await client.delete(
            f"/capture/expenses/{expense.id}", cookies=_cookies(), headers=csrf_headers
        )
        view_while_deleted = await client.get(f"/capture/expenses/{expense.id}", cookies=_cookies())
        edit_while_deleted = await client.patch(
            f"/capture/expenses/{expense.id}",
            cookies=_cookies(),
            headers=csrf_headers,
            json={"note": "x"},
        )
        restored = await client.post(
            f"/capture/expenses/{expense.id}/restore", cookies=_cookies(), headers=csrf_headers
        )
        restored_again = await client.post(
            f"/capture/expenses/{expense.id}/restore", cookies=_cookies(), headers=csrf_headers
        )
        view_after_restore = await client.get(f"/capture/expenses/{expense.id}", cookies=_cookies())

    assert deleted.status_code == deleted_again.status_code == 204
    assert deleted.content == b""
    assert deleted.headers["cache-control"] == "no-store"
    assert view_while_deleted.status_code == edit_while_deleted.status_code == 404
    assert restored.status_code == restored_again.status_code == 200
    assert view_after_restore.json() == restored.json() == restored_again.json()
    assert restored.json()["modified_at"] == (NOW + timedelta(minutes=10)).isoformat().replace(
        "+00:00", "Z"
    )
    assert expenses.store[expense.id] == replace(expense, modified_at=NOW + timedelta(minutes=10))
    assert [record.change_type.value for record in change_records.records] == [
        "deleted",
        "restored",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "suffix", "dependency"),
    [
        pytest.param("DELETE", "", get_delete_expense, id="delete"),
        pytest.param("POST", "/restore", get_restore_expense, id="restore"),
    ],
)
async def test_delete_and_restore_csrf_failure_never_invokes_the_use_case(
    settings: Settings, method: str, suffix: str, dependency: Callable[..., object]
) -> None:
    application, expenses, _change_records, expense = _app_with_edit(settings)
    recorder = RecordingDeletionStateChange()
    application.dependency_overrides[dependency] = lambda: recorder
    valid = _csrf_headers(application, settings)
    invalid_cases = [
        {},
        {"Origin": valid["Origin"]},
        {**valid, CSRF_HEADER_NAME: "wrong-token"},
        {**valid, "Origin": "https://attacker.test"},
    ]

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        responses = [
            await client.request(
                method,
                f"/capture/expenses/{expense.id}{suffix}",
                cookies=_cookies(),
                headers=headers,
            )
            for headers in invalid_cases
        ]

    assert [response.status_code for response in responses] == [403] * len(invalid_cases)
    assert recorder.calls == []
    assert expenses.store[expense.id] == expense


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "suffix"),
    [pytest.param("DELETE", "", id="delete"), pytest.param("POST", "/restore", id="restore")],
)
async def test_delete_and_restore_of_foreign_or_unknown_expenses_match_not_found(
    settings: Settings, method: str, suffix: str
) -> None:
    application, expenses, change_records, expense = _app_with_edit(settings)
    foreign = replace(expense, id=uuid4(), owner_id=uuid4()).delete(now=NOW)
    expenses.store[foreign.id] = foreign
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        foreign_response = await client.request(
            method,
            f"/capture/expenses/{foreign.id}{suffix}",
            cookies=_cookies(),
            headers=csrf_headers,
        )
        unknown_response = await client.request(
            method, f"/capture/expenses/{uuid4()}{suffix}", cookies=_cookies(), headers=csrf_headers
        )
        view_response = await client.get(f"/capture/expenses/{uuid4()}", cookies=_cookies())

    assert foreign_response.status_code == unknown_response.status_code == 404
    assert foreign_response.json() == unknown_response.json() == view_response.json()
    assert expenses.store[foreign.id] == foreign
    assert change_records.records == []


@pytest.mark.anyio
async def test_delete_and_restore_require_authentication(settings: Settings) -> None:
    application, _expenses, _change_records, expense = _app_with_edit(settings)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )
    csrf_headers = _csrf_headers(application, settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        delete_response = await client.delete(
            f"/capture/expenses/{expense.id}", cookies=_cookies(), headers=csrf_headers
        )
        restore_response = await client.post(
            f"/capture/expenses/{expense.id}/restore", cookies=_cookies(), headers=csrf_headers
        )

    assert delete_response.status_code == restore_response.status_code == 401


def _add_history(
    expenses: FakeExpenseRepository, owner_id: UUID, *, count: int, category_key: str = "groceries"
) -> list[Expense]:
    added = []
    for day in range(count):
        expense = Expense.create(
            owner_id=owner_id,
            money=Money(minor_units=100 + day, currency=CurrencyCode("USD")),
            transaction_date=TransactionDate(NOW.date() - timedelta(days=day)),
            category_key=category_key,
            capture_draft_id=uuid4(),
            source=CaptureSource.WEB_MANUAL,
            now=NOW,
        )
        expenses.store[expense.id] = expense
        added.append(expense)
    return added


@pytest.mark.anyio
async def test_list_expenses_pages_through_the_callers_active_history(settings: Settings) -> None:
    application, expenses, _change_records, expense = _app_with_edit(settings)
    own = _add_history(expenses, expense.owner_id, count=4)
    _add_history(expenses, uuid4(), count=3)
    deleted = replace(own[1], id=uuid4()).delete(now=NOW)
    expenses.store[deleted.id] = deleted

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        pages = []
        params: dict[str, str] = {"limit": "2"}
        while True:
            response = await client.get("/capture/expenses", params=params, cookies=_cookies())
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            pages.append(response.json())
            if response.json()["next_cursor"] is None:
                break
            params = {"limit": "2", "cursor": response.json()["next_cursor"]}

    ids = [item["id"] for page in pages for item in page["items"]]
    assert [len(page["items"]) for page in pages] == [2, 2, 1]
    assert len(ids) == len(set(ids)) == 5
    assert set(ids) == {str(item.id) for item in [expense, *own]}
    assert set(pages[0]["items"][0]) == {
        "id",
        "amount_minor_units",
        "currency",
        "transaction_date",
        "merchant",
        "category_key",
        "note",
        "source",
        "capture_draft_id",
        "created_at",
        "modified_at",
    }


@pytest.mark.anyio
async def test_list_expenses_unknown_category_is_an_empty_result(settings: Settings) -> None:
    application, _expenses, _change_records, _expense = _app_with_edit(settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.get(
            "/capture/expenses", params={"category": "not_a_category"}, cookies=_cookies()
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"date_from": "2026-08-05", "date_to": "2026-08-04"}, id="inverted range"),
        pytest.param({"date_from": "yesterday"}, id="bad date"),
        pytest.param({"currency": "ZZZ"}, id="bad currency"),
        pytest.param({"limit": "500"}, id="limit too large"),
        pytest.param({"cursor": "c2VjcmV0LWlucHV0"}, id="malformed cursor"),
        pytest.param({"merchant": "secret-input"}, id="unknown parameter"),
    ],
)
async def test_list_expenses_rejects_invalid_parameters_without_echoing_them(
    settings: Settings, params: dict[str, str]
) -> None:
    application, _expenses, _change_records, _expense = _app_with_edit(settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.get("/capture/expenses", params=params, cookies=_cookies())

    assert response.status_code == 422
    assert response.json() == {"detail": "The request body is invalid."}
    for value in params.values():
        assert value not in response.text


@pytest.mark.anyio
async def test_list_expenses_cursor_from_another_user_reveals_nothing_of_theirs(
    settings: Settings,
) -> None:
    application, expenses, _change_records, expense = _app_with_edit(settings)
    stranger_id = uuid4()
    stranger_history = _add_history(expenses, stranger_id, count=5)
    # A cursor positioned inside the stranger's history, as they would receive it.
    foreign_cursor = encode_history_cursor(ExpenseHistoryPosition.after(stranger_history[0]))

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.get(
            "/capture/expenses", params={"cursor": foreign_cursor}, cookies=_cookies()
        )

    assert response.status_code == 200
    returned = {item["id"] for item in response.json()["items"]}
    assert returned <= {str(expense.id)}
    assert not returned & {str(item.id) for item in stranger_history}


@pytest.mark.anyio
async def test_list_expenses_requires_authentication(settings: Settings) -> None:
    application, _expenses, _change_records, _expense = _app_with_edit(settings)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        response = await client.get("/capture/expenses", cookies=_cookies())

    assert response.status_code == 401
