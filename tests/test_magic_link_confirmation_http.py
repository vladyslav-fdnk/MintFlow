import logging
import re
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response

from mintflow.application.authentication.login_challenge import (
    AuthenticatedUserIdentity,
    MagicLinkConsumptionResult,
)
from mintflow.config import Settings
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    get_consume_magic_link,
)
from mintflow.logging import UVICORN_ACCESS_LOGGER_NAME
from mintflow.main import create_app

TOKEN = "A" * 43
PATH = f"/auth/magic-link?token={TOKEN}&return_target=dashboard"


class RecordingMagicLinkConsumption:
    def __init__(self, result: MagicLinkConsumptionResult) -> None:
        self.result = result
        self.tokens: list[str] = []

    def execute(self, *, token: str) -> MagicLinkConsumptionResult:
        self.tokens.append(token)
        return self.result


class FailingMagicLinkConsumption:
    def execute(self, *, token: str) -> MagicLinkConsumptionResult:
        raise RuntimeError("simulated application failure")


@pytest.mark.anyio
async def test_valid_looking_token_renders_fixed_internal_post_form(settings: Settings) -> None:
    response = await _request(settings, "GET", PATH)

    assert response.status_code == 200
    assert '<form method="post" action="/auth/magic-link">' in response.text
    assert f'name="token" value="{TOKEN}"' in response.text
    assert 'name="return_target" value="dashboard"' in response.text
    assert not re.search(r"(?:src|href|action)=[\"'](?:https?:)?//", response.text)
    assert "<script" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/auth/magic-link",
        "/auth/magic-link?token=short&return_target=dashboard",
        f"/auth/magic-link?token={TOKEN}&return_target=https://attacker.test",
    ],
)
async def test_malformed_or_missing_input_renders_one_generic_safe_state(
    settings: Settings, path: str
) -> None:
    response = await _request(settings, "GET", path)

    assert response.status_code == 400
    assert response.text == (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<title>Sign-in link unavailable | MintFlow</title></head><body>"
        "<main><h1>Sign-in link unavailable</h1>"
        "<p>This sign-in link cannot be used. Request a new link to continue.</p>"
        "</main></body></html>"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GET", "HEAD", "GET", "GET"])
async def test_scanner_like_repeated_requests_do_not_authenticate(
    settings: Settings, method: str
) -> None:
    response = await _request(settings, method, PATH)

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_token_and_complete_query_url_are_absent_from_logs(
    settings: Settings, app_logs: pytest.LogCaptureFixture
) -> None:
    application = create_app(settings)
    access_logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)

    with app_logs.at_level(logging.DEBUG):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(PATH)
        access_logger.info('127.0.0.1 - "GET %s HTTP/1.1" 200', PATH)

    assert response.status_code == 200
    assert TOKEN not in app_logs.text
    assert PATH not in app_logs.text


@pytest.mark.anyio
async def test_post_consumes_and_sets_only_fresh_authenticated_cookie(settings: Settings) -> None:
    consumption = RecordingMagicLinkConsumption(
        MagicLinkConsumptionResult(
            identity=AuthenticatedUserIdentity(user_id=uuid4()),
            return_target="dashboard",
            session_secret="fresh-session-secret",
        )
    )
    application = create_app(settings)
    application.dependency_overrides[get_consume_magic_link] = lambda: consumption
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="https://app.mintflow.test",
        follow_redirects=False,
        cookies={AUTHENTICATED_SESSION_COOKIE_NAME: "existing-browser-secret"},
    ) as client:
        response = await client.post(
            "/auth/magic-link",
            headers={"Origin": "https://app.mintflow.test"},
            data={"token": TOKEN, "return_target": "https://attacker.test"},
        )

    assert consumption.tokens == [TOKEN]
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert "fresh-session-secret" in response.headers["set-cookie"]
    assert "existing-browser-secret" not in response.headers["set-cookie"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "origin",
    [None, "null", "https://attacker.test", "https://app.mintflow.test/"],
)
async def test_post_rejects_unapproved_origin_without_consuming(
    settings: Settings, origin: str | None
) -> None:
    consumption = RecordingMagicLinkConsumption(
        MagicLinkConsumptionResult(
            identity=AuthenticatedUserIdentity(user_id=uuid4()),
            return_target="dashboard",
            session_secret="unused",
        )
    )
    application = create_app(settings)
    application.dependency_overrides[get_consume_magic_link] = lambda: consumption
    headers = {} if origin is None else {"Origin": origin}
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://app.mintflow.test") as client:
        response = await client.post("/auth/magic-link", headers=headers, data={"token": TOKEN})

    assert response.status_code == 400
    assert consumption.tokens == []
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_invalid_consumption_has_generic_failure_and_no_cookie(settings: Settings) -> None:
    consumption = RecordingMagicLinkConsumption(
        MagicLinkConsumptionResult(identity=None, return_target=None)
    )
    application = create_app(settings)
    application.dependency_overrides[get_consume_magic_link] = lambda: consumption
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://app.mintflow.test") as client:
        response = await client.post(
            "/auth/magic-link",
            headers={"Origin": "https://app.mintflow.test"},
            data={"token": TOKEN},
        )

    assert response.status_code == 400
    assert "set-cookie" not in response.headers
    assert response.text == (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<title>Sign-in link unavailable | MintFlow</title></head><body>"
        "<main><h1>Sign-in link unavailable</h1>"
        "<p>This sign-in link cannot be used. Request a new link to continue.</p>"
        "</main></body></html>"
    )


@pytest.mark.anyio
async def test_application_failure_emits_no_cookie_or_secret_logs(
    settings: Settings, app_logs: pytest.LogCaptureFixture
) -> None:
    existing_cookie = "existing-browser-secret"
    application = create_app(settings)
    application.dependency_overrides[get_consume_magic_link] = lambda: FailingMagicLinkConsumption()
    transport = ASGITransport(app=application, raise_app_exceptions=False)

    with app_logs.at_level(logging.DEBUG):
        async with AsyncClient(
            transport=transport,
            base_url="https://app.mintflow.test",
            cookies={AUTHENTICATED_SESSION_COOKIE_NAME: existing_cookie},
        ) as client:
            response = await client.post(
                "/auth/magic-link",
                headers={"Origin": "https://app.mintflow.test"},
                data={"token": TOKEN},
            )

    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert TOKEN not in app_logs.text
    assert existing_cookie not in app_logs.text


async def _request(settings: Settings, method: str, path: str) -> Response:
    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path)
