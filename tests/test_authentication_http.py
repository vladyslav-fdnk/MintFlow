import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from mintflow.application.authentication import AuthenticatedWebSession, ConsumeMagicLink
from mintflow.config import Settings
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    AuthenticatedPrincipalDependency,
    NormalizedNetworkSource,
    get_authenticate_web_session,
    get_authentication_use_cases,
    get_consume_magic_link,
    get_database_session,
    get_normalized_network_source,
)
from mintflow.main import create_app


class TrackingSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RecordingMagicLinkRequest:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def execute(
        self, *, submitted_email: str, normalized_network_source: str, return_target: str
    ) -> object:
        self.calls.append(
            {
                "submitted_email": submitted_email,
                "normalized_network_source": normalized_network_source,
                "return_target": return_target,
            }
        )
        return object()


class StubSessionAuthentication:
    def __init__(self, result: AuthenticatedWebSession | None) -> None:
        self.result = result

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        return self.result


def add_protected_test_route(application: FastAPI) -> None:
    @application.get("/test/protected")
    async def protected(principal: AuthenticatedPrincipalDependency) -> dict[str, str]:
        return {
            "user_id": str(principal.user_id),
            "web_session_id": str(principal.web_session_id),
        }


@pytest.mark.anyio
async def test_valid_cookie_resolves_typed_principal_for_protected_handler(
    settings: Settings,
) -> None:
    application = create_app(settings)
    authenticated = AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4())
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(authenticated)
    )
    add_protected_test_route(application)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(
            "/test/protected",
            cookies={AUTHENTICATED_SESSION_COOKIE_NAME: "A" * 43},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(authenticated.user_id),
        "web_session_id": str(authenticated.session_id),
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cookie_value", "authenticated"),
    [
        (None, False),
        ("", False),
        ("malformed", False),
        ("A" * 43, False),
    ],
)
async def test_protected_handler_returns_uniform_unauthenticated_response(
    settings: Settings, cookie_value: str | None, authenticated: bool
) -> None:
    application = create_app(settings)
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(None)
    )
    add_protected_test_route(application)
    cookies = {} if cookie_value is None else {AUTHENTICATED_SESSION_COOKIE_NAME: cookie_value}

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/test/protected", cookies=cookies)

    assert not authenticated
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "status_code"),
    [("/test/success", 200), ("/test/handled", 400), ("/test/error", 500)],
)
async def test_request_session_is_closed_after_success_and_exception(
    settings: Settings, path: str, status_code: int
) -> None:
    application = FastAPI()
    sessions: list[TrackingSession] = []

    def session_factory() -> TrackingSession:
        session = TrackingSession()
        sessions.append(session)
        return session

    application.state.authentication_runtime = type(
        "Runtime", (), {"session_factory": staticmethod(session_factory)}
    )()

    @application.get("/test/success")
    async def success(session: Annotated[object, Depends(get_database_session)]) -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/test/error")
    async def error(session: Annotated[object, Depends(get_database_session)]) -> None:
        raise RuntimeError("safe failure")

    @application.get("/test/handled", status_code=400)
    async def handled(session: Annotated[object, Depends(get_database_session)]) -> dict[str, str]:
        return {"status": "rejected"}

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == status_code
    assert len(sessions) == 1
    assert sessions[0].closed


@pytest.mark.anyio
async def test_consumption_dependency_preserves_request_session_cleanup_without_email_sender() -> (
    None
):
    application = FastAPI()
    sessions: list[TrackingSession] = []

    def session_factory() -> TrackingSession:
        session = TrackingSession()
        sessions.append(session)
        return session

    application.state.authentication_runtime = SimpleNamespace(
        session_factory=session_factory,
        email_sender=None,
        clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
    )

    @application.get("/test/consume-dependency")
    async def resolve_consumption(
        consume: Annotated[ConsumeMagicLink, Depends(get_consume_magic_link)],
    ) -> dict[str, str]:
        assert isinstance(consume, ConsumeMagicLink)
        return {"status": "ok"}

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test/consume-dependency")

    assert response.status_code == 200
    assert len(sessions) == 1
    assert sessions[0].closed


@pytest.mark.anyio
async def test_forwarding_headers_do_not_change_http_network_source(settings: Settings) -> None:
    application = create_app(settings)

    @application.get("/test/network-source")
    async def normalized_source(
        source: Annotated[NormalizedNetworkSource, Depends(get_normalized_network_source)],
    ) -> dict[str, str]:
        return {"source": source.value}

    transport = ASGITransport(app=application, client=("192.0.2.10", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/test/network-source",
            headers={
                "Forwarded": "for=203.0.113.9",
                "X-Forwarded-For": "198.51.100.7",
            },
        )

    assert response.json() == {"source": "192.0.2.10"}


@pytest.mark.anyio
async def test_unexpected_exception_response_and_logs_do_not_disclose_secrets(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    application = create_app(settings)
    sensitive_values = [
        settings.database_url.get_secret_value(),
        settings.authentication_rate_limit_key.get_secret_value(),
        "raw-token",
        "session-cookie",
        "Bearer authorization-secret",
    ]

    @application.get("/test/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("authentication request failed")

    with caplog.at_level(logging.ERROR):
        transport = ASGITransport(app=application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/test/unexpected",
                headers={
                    "Authorization": "Bearer authorization-secret",
                    "Cookie": "session=session-cookie",
                    "X-Test-Token": "raw-token",
                },
            )

    captured = response.text + caplog.text
    assert response.status_code == 500
    for sensitive_value in sensitive_values:
        assert sensitive_value not in captured


@pytest.mark.anyio
async def test_magic_link_request_maps_body_and_direct_peer(settings: Settings) -> None:
    application = create_app(settings)
    recorder = RecordingMagicLinkRequest()
    application.dependency_overrides[get_authentication_use_cases] = lambda: SimpleNamespace(
        request_magic_link=recorder
    )

    transport = ASGITransport(app=application, client=("192.0.2.10", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/magic-link/request",
            json={"email": "person@example.com", "return_target": "dashboard"},
            headers={
                "Forwarded": "for=203.0.113.9",
                "X-Forwarded-For": "198.51.100.7",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": "If the address can receive email, a sign-in link will arrive shortly."
    }
    assert recorder.calls == [
        {
            "submitted_email": "person@example.com",
            "normalized_network_source": "192.0.2.10",
            "return_target": "dashboard",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"{}",
        b'{"email":"person@example.com","return_target":"dashboard","extra":true}',
        b'{"email":"' + (b"a" * 321) + b'","return_target":"dashboard"}',
        b"x" * 1_025,
    ],
)
async def test_malformed_and_oversized_requests_receive_generic_response(
    settings: Settings, content: bytes
) -> None:
    application = create_app(settings)
    recorder = RecordingMagicLinkRequest()
    application.dependency_overrides[get_authentication_use_cases] = lambda: SimpleNamespace(
        request_magic_link=recorder
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/magic-link/request",
            content=content,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": "If the address can receive email, a sign-in link will arrive shortly."
    }
    assert recorder.calls == []
