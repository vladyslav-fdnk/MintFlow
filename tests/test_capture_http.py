from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.config import Settings
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Money,
    TransactionDate,
)
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

    def update(self, draft: CaptureDraft) -> None:
        self.store[draft.id] = draft
        self.update_calls.append(draft)


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
