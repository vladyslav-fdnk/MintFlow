import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.telegram import TelegramConnection, TelegramLinkChallenge
from mintflow.config import Settings
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
    get_authenticate_web_session,
)
from mintflow.http.telegram import get_telegram_link_repository, get_telegram_update_handler
from mintflow.main import create_app
from mintflow.telegram import TelegramUpdate, messages
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SESSION_SECRET = "A" * 43
TELEGRAM_USER_ID = 424242


class StubSessionAuthentication:
    def __init__(self, result: AuthenticatedWebSession | None) -> None:
        self.result = result

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        return self.result


class FakeLinkRepository:
    """In-memory stand-in recording every call; the real one is covered on PostgreSQL."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.challenges: dict[UUID, TelegramLinkChallenge] = {}
        self.connections: dict[UUID, TelegramConnection] = {}
        self.confirm_succeeds = True

    def create_challenge(
        self,
        *,
        token_hash: bytes,
        user_id: UUID,
        web_session_id: UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> TelegramLinkChallenge:
        self.calls.append("create_challenge")
        challenge = TelegramLinkChallenge(
            id=uuid4(),
            initiating_user_id=user_id,
            initiating_web_session_id=web_session_id,
            issued_at=issued_at,
            expires_at=expires_at,
            claimed_at=None,
            claimed_telegram_user_id=None,
            claimed_telegram_display_name=None,
            confirmed_at=None,
        )
        self.challenges[challenge.id] = challenge
        return challenge

    def claim(self, **kwargs: object) -> TelegramLinkChallenge | None:
        raise AssertionError("the Web never claims")

    def challenge_for_session(
        self, *, challenge_id: UUID, web_session_id: UUID
    ) -> TelegramLinkChallenge | None:
        self.calls.append("challenge_for_session")
        challenge = self.challenges.get(challenge_id)
        if challenge is None or challenge.initiating_web_session_id != web_session_id:
            return None
        return challenge

    def confirm(
        self, *, challenge_id: UUID, web_session_id: UUID, now: datetime
    ) -> TelegramConnection | None:
        self.calls.append("confirm")
        challenge = self.challenge_for_session(
            challenge_id=challenge_id, web_session_id=web_session_id
        )
        if challenge is None or not self.confirm_succeeds:
            return None
        connection = TelegramConnection(
            id=uuid4(),
            user_id=challenge.initiating_user_id,
            telegram_user_id=TELEGRAM_USER_ID,
            telegram_display_name="Ada (@ada)",
            linked_at=now,
            unlinked_at=None,
        )
        self.connections[connection.user_id] = connection
        return connection

    def active_connection_for_user(self, *, user_id: UUID) -> TelegramConnection | None:
        self.calls.append("active_connection_for_user")
        return self.connections.get(user_id)

    def active_connection_for_telegram_user(
        self, *, telegram_user_id: int
    ) -> TelegramConnection | None:
        raise AssertionError("not used by the Web routes")

    def unlink(self, *, user_id: UUID, now: datetime) -> bool:
        self.calls.append("unlink")
        return self.connections.pop(user_id, None) is not None


class Harness:
    def __init__(self, settings: Settings, *, telegram_enabled: bool = True) -> None:
        self.application: FastAPI = create_app(settings)
        self.session = AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4())
        self.bot = RecordingTelegramBotApi()
        self.repository = FakeLinkRepository()
        self.application.state.telegram_runtime = (
            TelegramRuntime(
                bot_api=self.bot,
                bot_username="mintflow_test_bot",
                webhook_secret=SecretStr("hook-secret"),
                web_origin=settings.authentication_web_origin,
                clock=lambda: NOW,
            )
            if telegram_enabled
            else None
        )
        self.authenticate_as(self.session)
        self.application.dependency_overrides[get_telegram_link_repository] = lambda: (
            self.repository
        )
        self.settings = settings

    def authenticate_as(self, session: AuthenticatedWebSession | None) -> None:
        self.application.dependency_overrides[get_authenticate_web_session] = lambda: (
            StubSessionAuthentication(session)
        )

    def csrf_headers(self) -> dict[str, str]:
        runtime: AuthenticationRuntime = self.application.state.authentication_runtime
        token = runtime.csrf_digester.derive(session_secret=SESSION_SECRET)
        return {CSRF_HEADER_NAME: token, "Origin": self.settings.authentication_web_origin}

    async def request(
        self, method: str, path: str, *, headers: dict[str, str] | None = None
    ) -> Response:
        transport = ASGITransport(app=self.application)
        async with AsyncClient(
            transport=transport, base_url=self.settings.authentication_web_origin
        ) as client:
            return await client.request(
                method,
                path,
                cookies={AUTHENTICATED_SESSION_COOKIE_NAME: SESSION_SECRET},
                headers=headers,
            )


@pytest.mark.anyio
async def test_create_challenge_returns_a_deep_link_once(settings: Settings) -> None:
    harness = Harness(settings)

    response = await harness.request(
        "POST", "/telegram/link-challenges", headers=harness.csrf_headers()
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"challenge_id", "deep_link", "expires_at"}
    assert body["deep_link"].startswith("https://t.me/mintflow_test_bot?start=")
    assert body["expires_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert response.headers["cache-control"] == "no-store"

    token = body["deep_link"].split("?start=", 1)[1]
    status = await harness.request("GET", f"/telegram/link-challenges/{body['challenge_id']}")
    assert status.status_code == 200
    assert status.json()["state"] == "waiting_for_telegram"
    assert token not in status.text


@pytest.mark.anyio
async def test_other_sessions_see_the_same_404_as_for_an_unknown_challenge(
    settings: Settings,
) -> None:
    harness = Harness(settings)
    created = await harness.request(
        "POST", "/telegram/link-challenges", headers=harness.csrf_headers()
    )
    challenge_id = created.json()["challenge_id"]
    # Another session of the same User.
    harness.authenticate_as(
        AuthenticatedWebSession(session_id=uuid4(), user_id=harness.session.user_id)
    )

    foreign = await harness.request("GET", f"/telegram/link-challenges/{challenge_id}")
    unknown = await harness.request("GET", f"/telegram/link-challenges/{uuid4()}")
    confirm = await harness.request(
        "POST",
        f"/telegram/link-challenges/{challenge_id}/confirm",
        headers=harness.csrf_headers(),
    )

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert confirm.status_code == 409
    assert harness.bot.calls == []


@pytest.mark.anyio
async def test_confirm_links_and_the_bot_says_connected(settings: Settings) -> None:
    harness = Harness(settings)
    created = await harness.request(
        "POST", "/telegram/link-challenges", headers=harness.csrf_headers()
    )

    response = await harness.request(
        "POST",
        f"/telegram/link-challenges/{created.json()['challenge_id']}/confirm",
        headers=harness.csrf_headers(),
    )
    connection = await harness.request("GET", "/telegram/connection")

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "telegram_display_name": "Ada (@ada)",
        "linked_at": NOW.isoformat(),
    }
    assert connection.json() == response.json()
    [sent] = harness.bot.calls_to("send_message")
    assert sent.arguments["chat_id"] == TELEGRAM_USER_ID
    assert sent.arguments["text"] == messages.LINKED


@pytest.mark.anyio
async def test_a_failed_bot_acknowledgement_does_not_undo_the_link(
    settings: Settings, app_logs: pytest.LogCaptureFixture
) -> None:
    harness = Harness(settings)
    harness.bot.fail_methods.add("send_message")
    created = await harness.request(
        "POST", "/telegram/link-challenges", headers=harness.csrf_headers()
    )

    response = await harness.request(
        "POST",
        f"/telegram/link-challenges/{created.json()['challenge_id']}/confirm",
        headers=harness.csrf_headers(),
    )

    assert response.status_code == 200
    assert harness.repository.connections
    assert "telegram_link_ack_failed" in app_logs.text
    assert messages.LINKED not in app_logs.text


@pytest.mark.anyio
async def test_confirmation_failure_is_one_generic_409(settings: Settings) -> None:
    harness = Harness(settings)
    harness.repository.confirm_succeeds = False
    created = await harness.request(
        "POST", "/telegram/link-challenges", headers=harness.csrf_headers()
    )

    response = await harness.request(
        "POST",
        f"/telegram/link-challenges/{created.json()['challenge_id']}/confirm",
        headers=harness.csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "The Telegram account could not be connected. Please start again."
    }
    assert harness.bot.calls == []


@pytest.mark.anyio
async def test_unlink_is_204_and_idempotent(settings: Settings) -> None:
    harness = Harness(settings)
    created = await harness.request(
        "POST", "/telegram/link-challenges", headers=harness.csrf_headers()
    )
    await harness.request(
        "POST",
        f"/telegram/link-challenges/{created.json()['challenge_id']}/confirm",
        headers=harness.csrf_headers(),
    )

    first = await harness.request("DELETE", "/telegram/connection", headers=harness.csrf_headers())
    second = await harness.request("DELETE", "/telegram/connection", headers=harness.csrf_headers())
    status = await harness.request("GET", "/telegram/connection")

    assert first.status_code == second.status_code == 204
    assert status.json() == {"connected": False, "telegram_display_name": None, "linked_at": None}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/telegram/link-challenges"),
        ("POST", f"/telegram/link-challenges/{uuid4()}/confirm"),
        ("DELETE", "/telegram/connection"),
    ],
)
async def test_csrf_failures_never_reach_the_repository(
    settings: Settings, method: str, path: str
) -> None:
    harness = Harness(settings)
    valid = harness.csrf_headers()
    invalid_cases = [
        {},
        {"Origin": valid["Origin"]},
        {**valid, CSRF_HEADER_NAME: "wrong-token"},
        {**valid, "Origin": "https://attacker.test"},
    ]

    responses = [await harness.request(method, path, headers=headers) for headers in invalid_cases]

    assert [response.status_code for response in responses] == [403] * len(invalid_cases)
    assert harness.repository.calls == []
    assert harness.bot.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/telegram/link-challenges"),
        ("GET", f"/telegram/link-challenges/{uuid4()}"),
        ("POST", f"/telegram/link-challenges/{uuid4()}/confirm"),
        ("GET", "/telegram/connection"),
        ("DELETE", "/telegram/connection"),
    ],
)
async def test_every_route_is_404_when_telegram_is_not_configured(
    settings: Settings, method: str, path: str
) -> None:
    harness = Harness(settings, telegram_enabled=False)

    response = await harness.request(method, path, headers=harness.csrf_headers())

    assert response.status_code == 404
    assert harness.repository.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/telegram/link-challenges"),
        ("GET", f"/telegram/link-challenges/{uuid4()}"),
        ("GET", "/telegram/connection"),
        ("DELETE", "/telegram/connection"),
    ],
)
async def test_every_route_requires_authentication(
    settings: Settings, method: str, path: str
) -> None:
    harness = Harness(settings)
    harness.authenticate_as(None)

    response = await harness.request(method, path, headers=harness.csrf_headers())

    assert response.status_code == 401
    assert harness.repository.calls == []


class RecordingHandler:
    def __init__(self) -> None:
        self.updates: list[int] = []

    def handle(self, update: TelegramUpdate) -> None:
        self.updates.append(update.update_id)


def _webhook_harness(
    settings: Settings, *, telegram_enabled: bool = True
) -> tuple[Harness, RecordingHandler]:
    harness = Harness(settings, telegram_enabled=telegram_enabled)
    handler = RecordingHandler()
    harness.application.dependency_overrides[get_telegram_update_handler] = lambda: handler
    return harness, handler


async def _post_webhook(harness: Harness, *, secret: str | None, body: bytes) -> Response:
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    transport = ASGITransport(app=harness.application)
    async with AsyncClient(transport=transport, base_url="https://api.mintflow.test") as client:
        return await client.post("/telegram/webhook", content=body, headers=headers)


UPDATE = json.dumps(
    {
        "update_id": 77,
        "message": {
            "message_id": 1,
            "date": 0,
            "text": "/help",
            "from": {"id": 5, "is_bot": False, "first_name": "A"},
            "chat": {"id": 5, "type": "private"},
        },
    }
).encode()


@pytest.mark.anyio
async def test_webhook_passes_verified_updates_to_the_handler_without_cookies_or_csrf(
    settings: Settings,
) -> None:
    harness, handler = _webhook_harness(settings)

    response = await _post_webhook(harness, secret="hook-secret", body=UPDATE)

    assert response.status_code == 200
    assert handler.updates == [77]


@pytest.mark.anyio
@pytest.mark.parametrize("secret", [None, "", "wrong-secret", "hook-secret-but-longer"])
async def test_webhook_rejects_a_missing_or_wrong_secret_before_handling(
    settings: Settings, secret: str | None
) -> None:
    harness, handler = _webhook_harness(settings)

    response = await _post_webhook(harness, secret=secret, body=UPDATE)

    assert response.status_code == 401
    assert handler.updates == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'{"message": {}}',
        b'{"update_id": 1, "x": "' + b"a" * 300_000 + b'"}',
    ],
)
async def test_webhook_acknowledges_and_ignores_unusable_payloads(
    settings: Settings, body: bytes
) -> None:
    harness, handler = _webhook_harness(settings)

    response = await _post_webhook(harness, secret="hook-secret", body=body)

    assert response.status_code == 200
    assert handler.updates == []


@pytest.mark.anyio
async def test_webhook_is_404_when_telegram_is_not_configured(settings: Settings) -> None:
    harness, handler = _webhook_harness(settings, telegram_enabled=False)

    response = await _post_webhook(harness, secret="hook-secret", body=UPDATE)

    assert response.status_code == 404
    assert handler.updates == []
