from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.commands.telegram_polling import poll
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import AUTHENTICATED_SESSION_COOKIE_NAME, AuthenticationRuntime
from mintflow.http.capture import CaptureRuntime
from mintflow.http.telegram import build_telegram_update_handler
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import (
    CaptureDraftRecord,
    ExpenseRecord,
    TelegramConnectionRecord,
    TelegramConversationRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app
from mintflow.telegram import InlineButton, TelegramUpdate, messages
from mintflow.telegram.handler import TelegramUpdateHandler
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"
TELEGRAM_USER_ID = 777_777
WEB_SECRET = "W" * 43


def _application(database_url: str) -> tuple[FastAPI, RecordingTelegramBotApi]:
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
        clock=lambda: NOW,
    )
    application.state.capture_runtime = CaptureRuntime(clock=lambda: NOW)
    bot = RecordingTelegramBotApi()
    application.state.telegram_runtime = TelegramRuntime(
        bot_api=bot,
        bot_username="mintflow_test_bot",
        webhook_secret=SecretStr("hook-secret"),
        web_origin=ORIGIN,
        clock=lambda: NOW,
    )
    return application, bot


def _linked_user(session: Session) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=1))
    session.add(user)
    session.commit()
    session.execute(
        update(UserRecord).where(UserRecord.id == user.id).values(default_currency="EUR")
    )
    session.add(
        TelegramConnectionRecord(
            user_id=user.id, telegram_user_id=TELEGRAM_USER_ID, linked_at=NOW - timedelta(hours=1)
        )
    )
    session.add(
        WebSessionRecord(
            user_id=user.id,
            secret_hash=hash_token(WEB_SECRET),
            issued_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(days=1),
        )
    )
    session.commit()
    return user.id


_update_ids = count(10_000)


def _text(text: str) -> dict[str, object]:
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": 1,
            "date": 0,
            "text": text,
            "from": {"id": TELEGRAM_USER_ID, "is_bot": False, "first_name": "Ada"},
            "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
        },
    }


def _press(data: str, *, message_id: int = 50) -> dict[str, object]:
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": f"cb-{data[-12:]}",
            "from": {"id": TELEGRAM_USER_ID, "is_bot": False, "first_name": "Ada"},
            "message": {
                "message_id": message_id,
                "date": 0,
                "text": "card",
                "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
            },
            "data": data,
        },
    }


async def _deliver(application: FastAPI, update: dict[str, object]) -> None:
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://api.mintflow.test") as client:
        response = await client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        )
    assert response.status_code == 200


def _button(bot: RecordingTelegramBotApi, label: str) -> str:
    """The callback data of the most recently sent or edited button with this label."""
    for call in reversed(bot.calls):
        keyboard = call.arguments.get("keyboard")
        if not isinstance(keyboard, tuple):
            continue
        for row in keyboard:
            for button in row:
                if (
                    isinstance(button, InlineButton)
                    and button.text == label
                    and button.callback_data is not None
                ):
                    return button.callback_data
    raise AssertionError(f"no button labelled {label!r}")


def _sent_texts(bot: RecordingTelegramBotApi) -> list[str]:
    return [
        str(call.arguments["text"])
        for call in bot.calls
        if call.method in {"send_message", "edit_message_text"}
    ]


@pytest.mark.anyio
async def test_manual_capture_in_telegram_appears_on_the_web(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _linked_user(db_session)
    application, bot = _application(migrated_database_url)

    await _deliver(application, _text("/add"))
    await _deliver(application, _text("12,50"))
    await _deliver(application, _text("Corner Shop"))
    await _deliver(application, _press(_button(bot, "Groceries")))
    await _deliver(application, _press(_button(bot, "Confirm")))

    texts = _sent_texts(bot)
    assert texts[:3] == [messages.ASK_AMOUNT, messages.ASK_MERCHANT, messages.ASK_CATEGORY]
    assert "Amount: 12.50 EUR (default currency)" in texts[3]
    assert texts[-1] == messages.saved(amount="12.50 EUR")

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        history = await client.get(
            "/capture/expenses", cookies={AUTHENTICATED_SESSION_COOKIE_NAME: WEB_SECRET}
        )
    [item] = history.json()["items"]
    assert (item["amount_minor_units"], item["currency"]) == (1250, "EUR")
    assert (item["merchant"], item["category_key"]) == ("Corner Shop", "groceries")
    assert item["source"] == "telegram_manual"
    assert item["transaction_date"] == "2026-09-23"
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(ExpenseRecord)) == 1
    assert (
        db_session.scalar(
            select(CaptureDraftRecord.state).where(CaptureDraftRecord.owner_id == user_id)
        )
        == "confirmed"
    )
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_concurrent_confirm_taps_create_exactly_one_expense(
    db_session: Session, migrated_database_url: str
) -> None:
    _linked_user(db_session)
    application, bot = _application(migrated_database_url)
    for step in ("/add", "8", "Kiosk"):
        await _deliver(application, _text(step))
    await _deliver(application, _press(_button(bot, "Transport")))
    confirm_data = _button(bot, "Confirm")
    taps = [TelegramUpdate.model_validate(_press(confirm_data)) for _ in range(2)]
    bots = [RecordingTelegramBotApi(), RecordingTelegramBotApi()]
    barrier = Barrier(2)

    def tap(index: int) -> None:
        engine = create_database_engine(migrated_database_url)
        session = create_session_factory(engine)()
        try:
            handler = _handler_for(application, session, bots[index])
            barrier.wait(timeout=5)
            handler.handle(taps[index])
        finally:
            session.close()
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(tap, range(2)))

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(ExpenseRecord)) == 1
    answers = [
        call.arguments.get("text")
        for tap_bot in bots
        for call in tap_bot.calls_to("answer_callback_query")
    ]
    edits = [
        call.arguments["text"] for tap_bot in bots for call in tap_bot.calls_to("edit_message_text")
    ]
    assert edits == [messages.saved(amount="8.00 EUR")]
    assert sorted(str(answer) for answer in answers) == sorted(["None", messages.ALREADY_SAVED])
    application.state.database_engine.dispose()


def _handler_for(
    application: FastAPI, session: Session, bot: RecordingTelegramBotApi
) -> TelegramUpdateHandler:
    """The production composition, bound to an explicit session and bot."""
    runtime: TelegramRuntime = application.state.telegram_runtime
    return build_telegram_update_handler(session, replace(runtime, bot_api=bot))


@pytest.mark.anyio
async def test_unlinking_clears_the_conversation_and_stops_capture(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _linked_user(db_session)
    application, bot = _application(migrated_database_url)
    await _deliver(application, _text("/add"))

    assert SqlAlchemyTelegramLinkRepository(db_session).unlink(user_id=user_id, now=NOW)
    await _deliver(application, _text("12.50"))

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(TelegramConversationRecord)) == 0
    assert _sent_texts(bot)[-1] == messages.NOT_LINKED
    application.state.database_engine.dispose()


async def _save_through_bot(
    application: FastAPI, bot: RecordingTelegramBotApi, amount: str, merchant: str
) -> None:
    await _deliver(application, _text("/add"))
    await _deliver(application, _text(amount))
    await _deliver(application, _text(merchant))
    await _deliver(application, _press(_button(bot, "Groceries")))
    await _deliver(application, _press(_button(bot, "Confirm")))


@pytest.mark.anyio
async def test_recent_excludes_deleted_expenses(
    db_session: Session, migrated_database_url: str
) -> None:
    # Owner scoping of the same query is covered by the EXPENSE-01 history tests.
    _linked_user(db_session)
    application, bot = _application(migrated_database_url)
    await _save_through_bot(application, bot, "4", "Kept Shop")
    await _save_through_bot(application, bot, "9", "Deleted Shop")
    db_session.expire_all()
    deleted_id = db_session.scalar(
        select(ExpenseRecord.id).where(ExpenseRecord.merchant_name == "Deleted Shop")
    )
    db_session.execute(
        update(ExpenseRecord).where(ExpenseRecord.id == deleted_id).values(deleted_at=NOW)
    )
    db_session.commit()

    await _deliver(application, _text("/recent"))

    reply = _sent_texts(bot)[-1]
    assert reply.splitlines() == [
        messages.RECENT_HEADER,
        "23 Sep 2026 · Kept Shop · 4.00 EUR · Groceries",
    ]
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_discard_and_start_new_is_one_atomic_change(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _linked_user(db_session)
    application, bot = _application(migrated_database_url)
    await _deliver(application, _text("/add"))
    await _deliver(application, _text("7"))
    await _deliver(application, _text("/add"))

    await _deliver(application, _press(_button(bot, "Discard and start new")))

    db_session.expire_all()
    states = sorted(
        db_session.scalars(
            select(CaptureDraftRecord.state).where(CaptureDraftRecord.owner_id == user_id)
        ).all()
    )
    assert states == ["cancelled", "collecting"]
    active = db_session.scalar(
        select(TelegramConversationRecord.active_draft_id).where(
            TelegramConversationRecord.user_id == user_id
        )
    )
    collecting = db_session.scalar(
        select(CaptureDraftRecord.id).where(
            CaptureDraftRecord.owner_id == user_id, CaptureDraftRecord.state == "collecting"
        )
    )
    assert active == collecting
    assert _sent_texts(bot)[-1] == messages.ASK_AMOUNT
    application.state.database_engine.dispose()


def test_polling_uses_the_webhook_composition_including_deduplication(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _linked_user(db_session)
    application, _webhook_bot = _application(migrated_database_url)
    add = TelegramUpdate.model_validate(_text("/add"))
    poll_bot = RecordingTelegramBotApi(pending_updates=[add, add])
    engine = create_database_engine(migrated_database_url)
    session_factory = create_session_factory(engine)

    def handle(update: TelegramUpdate) -> None:
        with session_factory() as session:
            _handler_for(application, session, poll_bot).handle(update)

    rounds = iter([True, False])
    try:
        poll(poll_bot, handle, should_continue=lambda: next(rounds))
    finally:
        engine.dispose()

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CaptureDraftRecord)
            .where(CaptureDraftRecord.owner_id == user_id)
        )
        == 1
    )
    assert _sent_texts(poll_bot) == [messages.ASK_AMOUNT]
    application.state.database_engine.dispose()
