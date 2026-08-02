import logging
import re

import pytest
from httpx import ASGITransport, AsyncClient, Response

from mintflow.config import Settings
from mintflow.logging import UVICORN_ACCESS_LOGGER_NAME
from mintflow.main import create_app

TOKEN = "A" * 43
PATH = f"/auth/magic-link?token={TOKEN}&return_target=dashboard"


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
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    application = create_app(settings)
    access_logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)

    with caplog.at_level(logging.DEBUG):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(PATH)
        access_logger.info('127.0.0.1 - "GET %s HTTP/1.1" 200', PATH)

    assert response.status_code == 200
    assert TOKEN not in caplog.text
    assert PATH not in caplog.text


async def _request(settings: Settings, method: str, path: str) -> Response:
    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path)
