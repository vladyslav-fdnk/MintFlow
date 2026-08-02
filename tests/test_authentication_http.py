import logging
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from mintflow.config import Settings
from mintflow.http.authentication import (
    NormalizedNetworkSource,
    get_database_session,
    get_normalized_network_source,
)
from mintflow.main import create_app


class TrackingSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
