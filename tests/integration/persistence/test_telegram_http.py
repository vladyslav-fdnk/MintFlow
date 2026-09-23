from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.telegram import ClaimTelegramLink
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord
from mintflow.main import create_app
from mintflow.telegram import messages
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"
TELEGRAM_USER_ID = 555_555


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _application(database_url: str, clock: Clock) -> tuple[FastAPI, RecordingTelegramBotApi]:
    application = create_app(
        Settings(
            environment="test",
            log_level="CRITICAL",
            database_url=SecretStr(database_url),
            authentication_rate_limit_key=SecretStr("integration-rate-limit-key"),
            authentication_csrf_signing_key=SecretStr("integration-csrf-signing-key"),
            authentication_web_origin=ORIGIN,
            authentication_return_targets=frozenset({"dashboard"}),
            email_backend=None,
        )
    )
    auth_runtime: AuthenticationRuntime = application.state.authentication_runtime
    application.state.authentication_runtime = AuthenticationRuntime(
        session_factory=auth_runtime.session_factory,
        email_sender=auth_runtime.email_sender,
        link_builder=auth_runtime.link_builder,
        rate_limit_digester=auth_runtime.rate_limit_digester,
        csrf_digester=auth_runtime.csrf_digester,
        clock=clock,
    )
    bot = RecordingTelegramBotApi()
    application.state.telegram_runtime = TelegramRuntime(
        bot_api=bot,
        bot_username="mintflow_test_bot",
        webhook_secret=SecretStr("hook-secret"),
        web_origin=ORIGIN,
        clock=clock,
    )
    return application, bot


def _add_account(session: Session, secret: str) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=1))
    session.add(user)
    session.commit()
    session.add(
        WebSessionRecord(
            user_id=user.id,
            secret_hash=hash_token(secret),
            issued_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(days=1),
        )
    )
    session.commit()
    return user.id


class Browser:
    def __init__(self, application: FastAPI, secret: str) -> None:
        self.application = application
        self.secret = secret

    async def request(self, method: str, path: str, *, csrf: bool = True) -> Response:
        runtime: AuthenticationRuntime = self.application.state.authentication_runtime
        headers = (
            {
                CSRF_HEADER_NAME: runtime.csrf_digester.derive(session_secret=self.secret),
                "Origin": ORIGIN,
            }
            if csrf
            else {}
        )
        transport = ASGITransport(app=self.application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            return await client.request(
                method,
                path,
                cookies={AUTHENTICATED_SESSION_COOKIE_NAME: self.secret},
                headers=headers,
            )


def _bot_claims(database_url: str, deep_link: str, clock: Clock) -> bool:
    """What the bot will do on a private-chat /start <token> (TG-05), on its own connection."""
    engine = create_database_engine(database_url)
    session = create_session_factory(engine)()
    try:
        return ClaimTelegramLink(
            repository=SqlAlchemyTelegramLinkRepository(session), clock=clock
        ).execute(
            payload=deep_link.split("?start=", 1)[1],
            telegram_user_id=TELEGRAM_USER_ID,
            display_name="Ada (@ada)",
        )
    finally:
        session.close()
        engine.dispose()


@pytest.mark.anyio
async def test_full_linking_ceremony_through_the_routes(
    db_session: Session, migrated_database_url: str
) -> None:
    clock = Clock()
    application, bot = _application(migrated_database_url, clock)
    owner = Browser(application, "O" * 43)
    _add_account(db_session, owner.secret)

    created = await owner.request("POST", "/telegram/link-challenges")
    challenge_path = f"/telegram/link-challenges/{created.json()['challenge_id']}"
    waiting = await owner.request("GET", challenge_path)
    clock.now += timedelta(minutes=1)
    assert _bot_claims(migrated_database_url, created.json()["deep_link"], clock)
    claimed = await owner.request("GET", challenge_path)
    confirmed = await owner.request("POST", f"{challenge_path}/confirm")
    connection = await owner.request("GET", "/telegram/connection")

    assert created.status_code == 201
    assert waiting.json()["state"] == "waiting_for_telegram"
    assert claimed.json()["state"] == "awaiting_confirmation"
    assert claimed.json()["telegram_display_name"] == "Ada (@ada)"
    assert confirmed.status_code == 200
    assert connection.json()["connected"] is True
    [sent] = bot.calls_to("send_message")
    assert sent.arguments == {
        "chat_id": TELEGRAM_USER_ID,
        "text": messages.LINKED,
        "keyboard": None,
    }

    unlinked = await owner.request("DELETE", "/telegram/connection")
    after = await owner.request("GET", "/telegram/connection")
    assert unlinked.status_code == 204
    assert after.json()["connected"] is False
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_another_account_cannot_see_or_confirm_the_challenge(
    db_session: Session, migrated_database_url: str
) -> None:
    clock = Clock()
    application, bot = _application(migrated_database_url, clock)
    owner, stranger = Browser(application, "P" * 43), Browser(application, "Q" * 43)
    _add_account(db_session, owner.secret)
    _add_account(db_session, stranger.secret)
    created = await owner.request("POST", "/telegram/link-challenges")
    assert _bot_claims(migrated_database_url, created.json()["deep_link"], clock)
    challenge_path = f"/telegram/link-challenges/{created.json()['challenge_id']}"

    seen = await stranger.request("GET", challenge_path)
    confirmed = await stranger.request("POST", f"{challenge_path}/confirm")
    no_csrf = await owner.request("POST", f"{challenge_path}/confirm", csrf=False)

    assert seen.status_code == 404
    assert confirmed.status_code == 409
    assert no_csrf.status_code == 403
    assert (await stranger.request("GET", "/telegram/connection")).json()["connected"] is False
    assert (await owner.request("GET", "/telegram/connection")).json()["connected"] is False
    assert bot.calls == []
    application.state.database_engine.dispose()
