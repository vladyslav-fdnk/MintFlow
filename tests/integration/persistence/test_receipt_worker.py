import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.receipts import (
    AmountLabel,
    CurrencyCandidate,
    CurrencyEvidence,
    DateCandidate,
    MerchantCandidate,
    RecognitionOutput,
    TotalCandidate,
)
from mintflow.application.users import DeleteAccount
from mintflow.commands.receipt_worker import build_receipt_worker
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import AUTHENTICATED_SESSION_COOKIE_NAME, AuthenticationRuntime
from mintflow.http.capture import CaptureRuntime
from mintflow.infrastructure.persistence import (
    PostgreSQLAccountDeletionRepository,
    StoredImage,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import (
    CaptureDraftRecord,
    ExpenseRecord,
    ReceiptImageRecord,
    ReceiptRecord,
    RecognitionResultRecord,
    TelegramConnectionRecord,
    TelegramConversationRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.infrastructure.recognition.fake import FakeReceiptRecognizer
from mintflow.main import create_app
from mintflow.telegram import InlineButton, messages
from mintflow.telegram.receipt_worker import ImageStore, ReceiptOutcome, ReceiptWorker
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"
TELEGRAM_USER_ID = 888_888
WEB_SECRET = "R" * 43
JPEG = b"\xff\xd8\xff\xe0" + b"receipt-image"

FULL = RecognitionOutput(
    merchants=(MerchantCandidate("Corner Shop", 0.9),),
    dates=(DateCandidate((date(2026, 9, 20),), 0.9),),
    totals=(TotalCandidate(Decimal("12.50"), AmountLabel.TOTAL, 0.95),),
    currencies=(CurrencyCandidate("EUR", CurrencyEvidence.EXPLICIT_CODE, 0.95),),
)
NO_TOTAL = replace(FULL, totals=())


class World:
    """One linked user, the web application, the recording bot, and a worker factory."""

    def __init__(self, database_url: str, session: Session) -> None:
        self.session = session
        self.bot = RecordingTelegramBotApi(files={"file-1": JPEG})
        self.recognizer = FakeReceiptRecognizer(default=FULL)
        self.application = create_app(
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
        auth: AuthenticationRuntime = self.application.state.authentication_runtime
        self.application.state.authentication_runtime = replace(auth, clock=lambda: NOW)
        self.application.state.capture_runtime = CaptureRuntime(clock=lambda: NOW)
        self.runtime = TelegramRuntime(
            bot_api=self.bot,
            bot_username="mintflow_test_bot",
            webhook_secret=SecretStr("hook-secret"),
            web_origin=ORIGIN,
            clock=lambda: NOW,
        )
        self.application.state.telegram_runtime = self.runtime
        self.user_id = self._linked_user()
        self._update_ids = iter(range(50_000, 60_000))

    def _linked_user(self) -> UUID:
        user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=1))
        self.session.add(user)
        self.session.commit()
        self.session.execute(
            update(UserRecord).where(UserRecord.id == user.id).values(default_currency="EUR")
        )
        self.session.add_all(
            [
                TelegramConnectionRecord(
                    user_id=user.id, telegram_user_id=TELEGRAM_USER_ID, linked_at=NOW
                ),
                WebSessionRecord(
                    user_id=user.id,
                    secret_hash=hash_token(WEB_SECRET),
                    issued_at=NOW - timedelta(hours=1),
                    expires_at=NOW + timedelta(days=1),
                ),
            ]
        )
        self.session.commit()
        return user.id

    def worker(self, session: Session | None = None, **overrides: object) -> ReceiptWorker:
        worker = build_receipt_worker(session or self.session, self.runtime, self.recognizer)
        for name, value in overrides.items():
            setattr(worker, f"_{name}", value)
        return worker

    async def deliver(self, message: dict[str, object]) -> None:
        update_body = {
            "update_id": next(self._update_ids),
            "message": {
                "message_id": 1,
                "date": 0,
                "from": {"id": TELEGRAM_USER_ID, "is_bot": False, "first_name": "Ada"},
                "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                **message,
            },
        }
        await self._post(update_body)

    async def press(self, label: str) -> None:
        await self._post(
            {
                "update_id": next(self._update_ids),
                "callback_query": {
                    "id": f"cb-{label}",
                    "from": {"id": TELEGRAM_USER_ID, "is_bot": False, "first_name": "Ada"},
                    "message": {
                        "message_id": 77,
                        "date": 0,
                        "text": "card",
                        "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                    },
                    "data": self.button(label),
                },
            }
        )

    async def _post(self, body: dict[str, object]) -> None:
        transport = ASGITransport(app=self.application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="https://api.mintflow.test") as client:
            response = await client.post(
                "/telegram/webhook",
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
            )
        assert response.status_code == 200

    async def send_photo(self, file_id: str = "file-1") -> None:
        await self.deliver({"photo": [{"file_id": file_id, "width": 1000, "height": 1400}]})

    def button(self, label: str) -> str:
        for call in reversed(self.bot.calls):
            keyboard = call.arguments.get("keyboard")
            if not isinstance(keyboard, tuple):
                continue
            for row in keyboard:
                for button in row:
                    if isinstance(button, InlineButton) and button.text == label:
                        assert button.callback_data is not None
                        return button.callback_data
        raise AssertionError(f"no button {label!r}")

    def texts(self) -> list[str]:
        return [
            str(call.arguments["text"])
            for call in self.bot.calls
            if call.method in {"send_message", "edit_message_text"}
        ]

    def draft_state(self) -> str:
        self.session.expire_all()
        return str(
            self.session.scalar(
                select(CaptureDraftRecord.state).where(CaptureDraftRecord.owner_id == self.user_id)
            )
        )

    def receipt_state(self) -> str:
        self.session.expire_all()
        return str(self.session.scalar(select(ReceiptRecord.state)))


@pytest.fixture
def world(db_session: Session, migrated_database_url: str) -> Iterator[World]:
    world = World(migrated_database_url, db_session)
    yield world
    world.application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_a_recognized_receipt_becomes_a_review_card_and_then_an_expense(
    world: World,
) -> None:
    await world.send_photo()

    outcome = world.worker().process_next()

    assert outcome is ReceiptOutcome.READY_FOR_REVIEW
    card = world.texts()[-1]
    assert "Merchant: Corner Shop (from receipt)" in card
    assert "Date: 20 Sep 2026 (from receipt)" in card
    assert "Amount: 12.50 EUR (from receipt)" in card
    assert "Category: Uncategorized" in card
    assert world.receipt_state() == "recognized"
    assert world.session.scalar(select(func.count()).select_from(ReceiptImageRecord)) == 1

    await world.press("Confirm")

    transport = ASGITransport(app=world.application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        history = await client.get(
            "/capture/expenses", cookies={AUTHENTICATED_SESSION_COOKIE_NAME: WEB_SECRET}
        )
    [item] = history.json()["items"]
    assert (item["amount_minor_units"], item["currency"], item["merchant"]) == (
        1250,
        "EUR",
        "Corner Shop",
    )
    assert item["source"] == "telegram_receipt"
    world.session.expire_all()
    receipt_id = world.session.scalar(select(ReceiptRecord.id))
    assert world.session.scalar(select(ExpenseRecord.receipt_id)) == receipt_id


@pytest.mark.anyio
async def test_a_missing_total_asks_for_the_amount_then_shows_the_card(world: World) -> None:
    world.recognizer.default = NO_TOTAL
    await world.send_photo()

    assert world.worker().process_next() is ReceiptOutcome.NEEDS_AMOUNT
    assert world.texts()[-1] == messages.RECEIPT_NEEDS_AMOUNT
    assert world.draft_state() == "collecting"

    # Merchant and date were recognized and Uncategorized proposed: only the amount is asked.
    await world.deliver({"text": "8.40"})

    card = world.texts()[-1]
    assert "Merchant: Corner Shop (from receipt)" in card
    assert "Amount: 8.40 EUR (default currency)" in card
    assert "Date: 20 Sep 2026 (from receipt)" in card
    assert world.draft_state() == "ready_for_review"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["recognizer error", "not an image", "download failure"])
async def test_failures_fall_back_to_manual_entry(world: World, failure: str) -> None:
    if failure == "recognizer error":
        world.recognizer.failing.add(JPEG)
    elif failure == "not an image":
        world.bot.files["file-1"] = b"%PDF-1.7 not an image"
    else:
        world.bot.fail_methods.add("get_file")
    await world.send_photo()

    outcome = world.worker().process_next()

    assert outcome is ReceiptOutcome.FAILED
    assert world.texts()[-1] == messages.RECEIPT_FAILED
    assert world.draft_state() == "collecting"
    assert world.receipt_state() == "recognition_failed"
    await world.deliver({"text": "5"})
    assert world.texts()[-1] == messages.ASK_MERCHANT


@pytest.mark.anyio
async def test_a_slow_recognizer_times_out_into_manual_entry(world: World) -> None:
    class Slow(FakeReceiptRecognizer):
        def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
            time.sleep(1.0)
            return FULL

    await world.send_photo()
    worker = world.worker(recognizer=Slow(), timeout=0.05)

    assert worker.process_next() is ReceiptOutcome.FAILED
    assert world.texts()[-1] == messages.RECEIPT_FAILED


@pytest.mark.anyio
async def test_a_cancelled_draft_keeps_the_result_but_hears_nothing(world: World) -> None:
    await world.send_photo()
    await world.deliver({"text": "/cancel"})
    sent_before = len(world.texts())

    outcome = world.worker().process_next()

    assert outcome is ReceiptOutcome.DRAFT_CLOSED
    assert len(world.texts()) == sent_before
    assert world.draft_state() == "cancelled"
    assert world.session.scalar(select(func.count()).select_from(RecognitionResultRecord)) == 1


@pytest.mark.anyio
async def test_a_reclaimed_receipt_reuses_the_stored_image(world: World) -> None:
    await world.send_photo()
    receipt_id = world.session.scalar(select(ReceiptRecord.id))
    world.session.add(
        ReceiptImageRecord(
            receipt_id=receipt_id, media_type="image/jpeg", content=JPEG, stored_at=NOW
        )
    )
    world.session.commit()

    assert world.worker().process_next() is ReceiptOutcome.READY_FOR_REVIEW
    assert world.bot.calls_to("get_file") == []
    assert world.recognizer.calls == [JPEG]


@pytest.mark.anyio
async def test_two_workers_process_each_receipt_exactly_once(world: World, engine: Engine) -> None:
    await world.send_photo()
    await world.deliver({"text": "/cancel"})
    await world.send_photo()
    sessions = [create_session_factory(engine)() for _ in range(2)]
    barrier = Barrier(2)

    def drain(index: int) -> list[ReceiptOutcome]:
        worker = world.worker(sessions[index])
        barrier.wait(timeout=5)
        outcomes = []
        while (outcome := worker.process_next()) is not None:
            outcomes.append(outcome)
        return outcomes

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(drain, range(2)))
    finally:
        for session in sessions:
            session.close()

    outcomes = sorted(outcome.value for result in results for outcome in result)
    assert outcomes == ["draft_closed", "ready_for_review"]
    world.session.expire_all()
    assert world.session.scalar(select(func.count()).select_from(RecognitionResultRecord)) == 2


@pytest.mark.anyio
async def test_the_conversation_waits_for_the_amount_after_a_failure(world: World) -> None:
    world.recognizer.failing.add(JPEG)
    await world.send_photo()

    world.worker().process_next()

    world.session.expire_all()
    assert world.session.scalar(select(TelegramConversationRecord.awaiting)) == "amount"


# --- RCPT-06: delay message, "Enter manually", and late results ------------------------------


def _age_receipt(world: World, age: timedelta) -> None:
    world.session.execute(update(ReceiptRecord).values(created_at=NOW - age))
    world.session.commit()


@pytest.mark.anyio
async def test_the_delay_message_is_sent_once_after_thirty_seconds(world: World) -> None:
    await world.send_photo()
    worker = world.worker()

    _age_receipt(world, timedelta(seconds=29))
    assert worker.send_delay_notices() == 0

    _age_receipt(world, timedelta(seconds=30))
    assert worker.send_delay_notices() == 1
    assert world.texts()[-1] == messages.RECEIPT_TAKING_LONGER
    assert world.button(messages.RECEIPT_ENTER_MANUALLY).endswith(":manual")

    assert worker.send_delay_notices() == 0
    assert world.worker().send_delay_notices() == 0
    assert world.texts().count(messages.RECEIPT_TAKING_LONGER) == 1


@pytest.mark.anyio
async def test_no_delay_message_for_a_cancelled_or_finished_receipt(world: World) -> None:
    await world.send_photo()
    await world.deliver({"text": "/cancel"})
    await world.send_photo()
    world.worker().process_next()  # the first receipt: its draft was cancelled
    world.worker().process_next()  # the second: recognized
    _age_receipt(world, timedelta(minutes=5))

    assert world.worker().send_delay_notices() == 0
    assert messages.RECEIPT_TAKING_LONGER not in world.texts()


@pytest.mark.anyio
async def test_the_delay_message_is_sent_while_a_slow_recognizer_runs(world: World) -> None:
    clock = [NOW]

    class Slow(FakeReceiptRecognizer):
        def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
            clock[0] = NOW + timedelta(seconds=31)
            time.sleep(0.3)
            return FULL

    await world.send_photo()
    worker = world.worker(recognizer=Slow(), clock=lambda: clock[0], wait_slice=0.02)

    assert worker.process_next() is ReceiptOutcome.READY_FOR_REVIEW

    texts = world.texts()
    assert texts[-2] == messages.RECEIPT_TAKING_LONGER
    assert "Merchant: Corner Shop (from receipt)" in texts[-1]


@pytest.mark.anyio
async def test_a_late_result_fills_only_what_the_user_left_untouched(world: World) -> None:
    await world.send_photo()
    _age_receipt(world, timedelta(seconds=31))
    world.worker().send_delay_notices()

    await world.press(messages.RECEIPT_ENTER_MANUALLY)
    assert world.texts()[-1] == messages.ASK_AMOUNT
    await world.deliver({"text": "8.40"})
    assert world.texts()[-1] == messages.ASK_MERCHANT
    await world.press("Skip")
    await world.press("Groceries")
    assert "Date: 23 Sep 2026 (default)" in world.texts()[-1]

    assert world.worker().process_next() is ReceiptOutcome.LATE_RESULT

    card = world.texts()[-1]
    assert "Amount: 8.40 EUR (default currency)" in card
    assert "Category: Groceries" in card
    assert "Date: 20 Sep 2026 (from receipt)" in card
    assert "Merchant: Corner Shop (from receipt)" in card
    await world.press("Confirm")
    world.session.expire_all()
    expense = world.session.scalars(select(ExpenseRecord)).one()
    assert (expense.amount_minor_units, expense.category_key, expense.transaction_date) == (
        840,
        "groceries",
        date(2026, 9, 20),
    )


@pytest.mark.anyio
async def test_a_late_result_answers_the_pending_amount_question(world: World) -> None:
    await world.send_photo()
    _age_receipt(world, timedelta(seconds=31))
    world.worker().send_delay_notices()
    await world.press(messages.RECEIPT_ENTER_MANUALLY)

    assert world.worker().process_next() is ReceiptOutcome.LATE_RESULT

    assert "Amount: 12.50 EUR (from receipt)" in world.texts()[-1]
    assert world.draft_state() == "ready_for_review"
    world.session.expire_all()
    assert world.session.scalar(select(TelegramConversationRecord.awaiting)) == "nothing"


@pytest.mark.anyio
async def test_a_recognizer_raising_timeout_error_fails_at_once(world: World) -> None:
    class Raising(FakeReceiptRecognizer):
        def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
            raise TimeoutError

    await world.send_photo()
    started = time.monotonic()

    assert world.worker(recognizer=Raising()).process_next() is ReceiptOutcome.FAILED
    assert time.monotonic() - started < 5


# --- the account is deleted while its receipt is being processed (account deletion, A1) ---------


def _delete_account(engine: Engine, user_id: UUID) -> None:
    with Session(engine) as session:
        assert DeleteAccount(
            repository=PostgreSQLAccountDeletionRepository(session), clock=lambda: NOW
        ).execute(user_id=user_id)


class _DeletingBeforeTheImage:
    """An image store that deletes the account just before the worker stores the photo."""

    def __init__(self, inner: ImageStore, engine: Engine, user_id: UUID) -> None:
        self._inner = inner
        self._engine = engine
        self._user_id = user_id

    def get(self, *, receipt_id: UUID) -> StoredImage | None:
        return self._inner.get(receipt_id=receipt_id)

    def put(self, *, receipt_id: UUID, media_type: str, content: bytes, now: datetime) -> bool:
        _delete_account(self._engine, self._user_id)
        return self._inner.put(
            receipt_id=receipt_id, media_type=media_type, content=content, now=now
        )


class _DeletingWhileRecognizing:
    def __init__(self, engine: Engine, user_id: UUID) -> None:
        self._engine = engine
        self._user_id = user_id

    def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
        _delete_account(self._engine, self._user_id)
        return FULL


@pytest.mark.anyio
async def test_a_receipt_whose_account_is_deleted_before_its_image_is_skipped(
    world: World, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    await world.send_photo()
    sent_before = len(world.texts())
    worker = world.worker()
    worker._images = _DeletingBeforeTheImage(worker._images, engine, world.user_id)

    assert worker.process_next() is ReceiptOutcome.STALE

    # Nothing more is said in the chat of an account that no longer exists.
    assert len(world.texts()) == sent_before
    assert "receipt_worker_iteration_failed" not in caplog.text
    world.session.expire_all()
    assert world.session.scalar(select(func.count()).select_from(ReceiptRecord)) == 0


@pytest.mark.anyio
async def test_a_receipt_whose_account_is_deleted_during_recognition_is_skipped(
    world: World, engine: Engine
) -> None:
    await world.send_photo()
    sent_before = len(world.texts())
    worker = world.worker(recognizer=_DeletingWhileRecognizing(engine, world.user_id))

    assert worker.process_next() is ReceiptOutcome.STALE

    # Nothing more is said in the chat of an account that no longer exists.
    assert len(world.texts()) == sent_before
    world.session.expire_all()
    for table in (ReceiptRecord, CaptureDraftRecord, UserRecord):
        assert world.session.scalar(select(func.count()).select_from(table)) == 0
