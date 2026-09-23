from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mintflow.application.authentication import AuthenticatedWebSession, hash_token
from mintflow.application.telegram import (
    ClaimTelegramLink,
    IssueTelegramLinkChallenge,
    ResolveTelegramUser,
)
from mintflow.config import Settings
from mintflow.domain.user import User, UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyTelegramUpdateLedger,
    SqlAlchemyUserRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    TelegramProcessedUpdateRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app
from mintflow.telegram import TelegramUpdate, messages
from mintflow.telegram.handler import TelegramUpdateHandler
from mintflow.telegram.outgoing import Outgoing
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


def _start_update(
    update_id: int, token: str, telegram_user_id: int = TELEGRAM_USER_ID
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 0,
            "text": f"/start {token}",
            "from": {
                "id": telegram_user_id,
                "is_bot": False,
                "first_name": "Ada",
                "username": "ada",
            },
            "chat": {"id": telegram_user_id, "type": "private"},
        },
    }


async def _deliver(application: FastAPI, update: dict[str, object]) -> Response:
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://api.mintflow.test") as client:
        return await client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        )


@pytest.mark.anyio
async def test_linking_with_the_claim_delivered_through_the_webhook(
    db_session: Session, migrated_database_url: str
) -> None:
    clock = Clock()
    application, bot = _application(migrated_database_url, clock)
    owner = Browser(application, "R" * 43)
    _add_account(db_session, owner.secret)
    created = await owner.request("POST", "/telegram/link-challenges")
    token = created.json()["deep_link"].split("?start=", 1)[1]

    delivered = await _deliver(application, _start_update(1001, token))
    redelivered = await _deliver(application, _start_update(1001, token))
    confirmed = await owner.request(
        "POST", f"/telegram/link-challenges/{created.json()['challenge_id']}/confirm"
    )

    assert delivered.status_code == redelivered.status_code == 200
    assert confirmed.status_code == 200
    texts = [call.arguments["text"] for call in bot.calls_to("send_message")]
    assert texts == [messages.LINK_CLAIMED, messages.LINKED]
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_unlinked_senders_are_refused_and_nothing_but_the_update_id_is_stored(
    db_session: Session, migrated_database_url: str
) -> None:
    clock = Clock()
    application, bot = _application(migrated_database_url, clock)
    update = _start_update(2002, "unused")
    update["message"]["text"] = "12.50 coffee"  # type: ignore[index]

    response = await _deliver(application, update)

    assert response.status_code == 200
    assert [call.arguments["text"] for call in bot.calls_to("send_message")] == [
        messages.NOT_LINKED
    ]
    db_session.expire_all()
    counts = db_session.execute(
        text(
            "SELECT (SELECT count(*) FROM telegram_processed_updates), "
            "(SELECT count(*) FROM capture_drafts), "
            "(SELECT count(*) FROM telegram_link_challenges)"
        )
    ).one()
    assert tuple(counts) == (1, 0, 0)
    application.state.database_engine.dispose()


class UnusedCapture:
    """/start never reaches the capture flow."""

    def on_command(self, user: User, chat_id: int, command: str) -> list[Outgoing] | None:
        raise AssertionError("unexpected capture command")

    def on_text(self, user: User, chat_id: int, text: str) -> list[Outgoing]:
        raise AssertionError("unexpected capture text")

    def on_callback(
        self, user: User, chat_id: int, message_id: int, callback_query_id: str, data: str
    ) -> list[Outgoing] | None:
        raise AssertionError("unexpected capture callback")


def test_concurrent_redelivery_does_the_work_exactly_once(
    db_session: Session, migrated_database_url: str
) -> None:
    clock = Clock()
    owner_secret = "S" * 43
    user_id = _add_account(db_session, owner_secret)
    web_session_id = db_session.scalar(
        select(WebSessionRecord.id).where(WebSessionRecord.user_id == user_id)
    )
    assert web_session_id is not None
    issued = IssueTelegramLinkChallenge(
        repository=SqlAlchemyTelegramLinkRepository(db_session),
        bot_username="mintflow_test_bot",
        clock=clock,
    ).execute(session=AuthenticatedWebSession(session_id=web_session_id, user_id=user_id))
    update = TelegramUpdate.model_validate(
        _start_update(3003, issued.deep_link.split("?start=", 1)[1])
    )
    bots = [RecordingTelegramBotApi(), RecordingTelegramBotApi()]
    barrier = Barrier(2)

    def deliver(index: int) -> None:
        engine = create_database_engine(migrated_database_url)
        session = create_session_factory(engine)()
        links = SqlAlchemyTelegramLinkRepository(session)
        handler = TelegramUpdateHandler(
            ledger=SqlAlchemyTelegramUpdateLedger(session),
            resolve_user=ResolveTelegramUser(
                repository=links, user_repository=SqlAlchemyUserRepository(session)
            ),
            claim_link=ClaimTelegramLink(repository=links, clock=clock),
            capture=UnusedCapture(),
            bot_api=bots[index],
            web_origin=ORIGIN,
            clock=clock,
        )
        try:
            barrier.wait(timeout=5)
            handler.handle(update)
        finally:
            session.close()
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(deliver, range(2)))

    replies = [call for bot in bots for call in bot.calls_to("send_message")]
    assert [call.arguments["text"] for call in replies] == [messages.LINK_CLAIMED]
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(TelegramProcessedUpdateRecord)) == 1
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuthenticationAuditRecordModel)
            .where(AuthenticationAuditRecordModel.event_type == "telegram_link_claimed")
        )
        == 1
    )
