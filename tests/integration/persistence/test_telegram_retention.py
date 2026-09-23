import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.capture import ConfirmCaptureDraft
from mintflow.application.telegram.retention import CleanUpTelegramRetention
from mintflow.commands import telegram_retention_cleanup
from mintflow.config import get_settings
from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureDraftState,
    CaptureSource,
    CurrencyCode,
    Money,
    Receipt,
    RecognitionResult,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyReceiptImageStore,
    SqlAlchemyReceiptRepository,
    SqlAlchemyUserRepository,
)
from mintflow.infrastructure.persistence.models import (
    CaptureDraftRecord,
    ExpenseRecord,
    ReceiptImageRecord,
    ReceiptRecord,
    RecognitionResultRecord,
    TelegramConnectionRecord,
    TelegramConversationRecord,
    TelegramLinkChallengeRecord,
    TelegramProcessedUpdateRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.infrastructure.persistence.telegram_retention import (
    PostgreSQLTelegramRetentionRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)


def _cleanup(session: Session, *, batch_size: int = 100) -> dict[str, int]:
    return (
        CleanUpTelegramRetention(
            repository=PostgreSQLTelegramRetentionRepository(session), clock=lambda: NOW
        )
        .execute(batch_size=batch_size)
        .aggregate_counts()
    )


def _user(session: Session) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - 90 * DAY)
    session.add(user)
    session.commit()
    return user.id


def _web_session(session: Session, user_id: UUID) -> UUID:
    record = WebSessionRecord(
        user_id=user_id,
        secret_hash=hash_token(str(uuid4())),
        issued_at=NOW - DAY,
        expires_at=NOW + DAY,
    )
    session.add(record)
    session.commit()
    return record.id


def _challenge(
    session: Session, user_id: UUID, web_session_id: UUID, *, issued_at: datetime, confirmed: bool
) -> UUID:
    record = TelegramLinkChallengeRecord(
        token_hash=hash_token(str(uuid4())),
        initiating_user_id=user_id,
        initiating_web_session_id=web_session_id,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
        claimed_at=issued_at + timedelta(minutes=1) if confirmed else None,
        claimed_telegram_user_id=1 if confirmed else None,
        confirmed_at=issued_at + timedelta(minutes=2) if confirmed else None,
    )
    session.add(record)
    session.commit()
    return record.id


def _draft(session: Session, owner_id: UUID, *, modified_at: datetime) -> CaptureDraft:
    draft = CaptureDraft.start(
        owner_id=owner_id, source=CaptureSource.TELEGRAM_MANUAL, now=modified_at
    )
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    return draft


def _state(session: Session, draft_id: UUID) -> str | None:
    session.expire_all()
    return session.scalar(select(CaptureDraftRecord.state).where(CaptureDraftRecord.id == draft_id))


def test_processed_updates_are_kept_for_exactly_seven_days(db_session: Session) -> None:
    for update_id, processed_at in [(1, NOW - 7 * DAY), (2, NOW - 7 * DAY + SECOND)]:
        db_session.add(
            TelegramProcessedUpdateRecord(update_id=update_id, processed_at=processed_at)
        )
    db_session.commit()

    counts = _cleanup(db_session)

    assert counts["processed_updates_deleted"] == 1
    assert list(db_session.scalars(select(TelegramProcessedUpdateRecord.update_id))) == [2]


def test_link_challenges_are_kept_30_days_after_confirmation_or_expiry(
    db_session: Session,
) -> None:
    user_id = _user(db_session)
    web_session_id = _web_session(db_session, user_id)
    confirmed_old = _challenge(
        db_session,
        user_id,
        web_session_id,
        issued_at=NOW - 30 * DAY - timedelta(minutes=2),
        confirmed=True,
    )
    confirmed_recent = _challenge(
        db_session,
        user_id,
        web_session_id,
        issued_at=NOW - 30 * DAY - timedelta(minutes=1),
        confirmed=True,
    )
    expired_old = _challenge(
        db_session,
        user_id,
        web_session_id,
        issued_at=NOW - 30 * DAY - timedelta(minutes=5),
        confirmed=False,
    )
    unfinished = _challenge(db_session, user_id, web_session_id, issued_at=NOW, confirmed=False)

    counts = _cleanup(db_session)

    assert counts["link_challenges_deleted"] == 2
    remaining = set(db_session.scalars(select(TelegramLinkChallengeRecord.id)))
    assert remaining == {confirmed_recent, unfinished}
    assert confirmed_old not in remaining and expired_old not in remaining


def test_only_long_unlinked_connections_are_deleted(db_session: Session) -> None:
    active_user, old_user, recent_user = _user(db_session), _user(db_session), _user(db_session)
    db_session.add_all(
        [
            TelegramConnectionRecord(
                user_id=active_user, telegram_user_id=10, linked_at=NOW - 400 * DAY
            ),
            TelegramConnectionRecord(
                user_id=old_user,
                telegram_user_id=20,
                linked_at=NOW - 60 * DAY,
                unlinked_at=NOW - 30 * DAY,
            ),
            TelegramConnectionRecord(
                user_id=recent_user,
                telegram_user_id=30,
                linked_at=NOW - 60 * DAY,
                unlinked_at=NOW - 30 * DAY + SECOND,
            ),
        ]
    )
    db_session.commit()

    counts = _cleanup(db_session)

    assert counts["unlinked_connections_deleted"] == 1
    assert set(db_session.scalars(select(TelegramConnectionRecord.telegram_user_id))) == {10, 30}


def test_abandoned_drafts_expire_and_release_their_conversation(db_session: Session) -> None:
    user_id = _user(db_session)
    abandoned = _draft(db_session, user_id, modified_at=NOW - 7 * DAY)
    recent = _draft(db_session, user_id, modified_at=NOW - 7 * DAY + SECOND)
    db_session.add(
        TelegramConversationRecord(
            user_id=user_id,
            active_draft_id=abandoned.id,
            awaiting="amount",
            currency_is_default=False,
            updated_at=NOW - 7 * DAY,
        )
    )
    db_session.commit()

    counts = _cleanup(db_session)

    assert counts["drafts_expired"] == 1
    assert _state(db_session, abandoned.id) == CaptureDraftState.EXPIRED.value
    assert _state(db_session, recent.id) == CaptureDraftState.COLLECTING.value
    conversation = db_session.get(TelegramConversationRecord, user_id)
    assert conversation is not None
    assert (conversation.active_draft_id, conversation.awaiting) == (None, "nothing")
    assert db_session.scalar(select(func.count()).select_from(ExpenseRecord)) == 0


def test_cancelled_drafts_stay_and_abandoned_review_drafts_expire(db_session: Session) -> None:
    user_id = _user(db_session)
    old = NOW - 60 * DAY
    repository = SqlAlchemyCaptureDraftRepository(db_session)
    cancelled = _draft(db_session, user_id, modified_at=old)
    repository.update(cancelled.cancel(caller_id=user_id, now=old))
    ready = _draft(db_session, user_id, modified_at=old)
    ready = ready.set_amount(
        caller_id=user_id, amount=Money(minor_units=100, currency=CurrencyCode("EUR")), now=old
    ).set_transaction_date(caller_id=user_id, transaction_date=TransactionDate(old.date()), now=old)
    repository.update(ready.mark_ready_for_review(caller_id=user_id, now=old))

    counts = _cleanup(db_session)

    assert counts["drafts_expired"] == 1
    assert _state(db_session, cancelled.id) == CaptureDraftState.CANCELLED.value
    assert _state(db_session, ready.id) == CaptureDraftState.EXPIRED.value


def test_batches_are_bounded_and_reruns_are_idempotent(db_session: Session) -> None:
    for update_id in range(5):
        db_session.add(
            TelegramProcessedUpdateRecord(update_id=update_id, processed_at=NOW - 8 * DAY)
        )
    db_session.commit()

    counts = [_cleanup(db_session, batch_size=2)["processed_updates_deleted"] for _ in range(4)]

    assert counts == [2, 2, 1, 0]


def test_concurrent_runs_never_double_count(engine: Engine) -> None:
    with Session(engine) as setup:
        user_id = _user(setup)
        for _ in range(20):
            _draft(setup, user_id, modified_at=NOW - 8 * DAY)
    barrier = Barrier(2)

    def run() -> int:
        with Session(engine) as session:
            service = CleanUpTelegramRetention(
                repository=PostgreSQLTelegramRetentionRepository(session), clock=lambda: NOW
            )
            barrier.wait(timeout=5)
            return service.execute(batch_size=20).drafts_expired

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: run(), range(2)))

    assert sum(counts) == 20
    with Session(engine) as session:
        states = set(session.scalars(select(CaptureDraftRecord.state)))
    assert states == {CaptureDraftState.EXPIRED.value}


def test_command_prints_counts_and_exits_zero(
    db_session: Session,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_session.add(
        TelegramProcessedUpdateRecord(update_id=99, processed_at=datetime(2000, 1, 1, tzinfo=UTC))
    )
    db_session.commit()
    for name, value in {
        "MINTFLOW_DATABASE_URL": migrated_database_url,
        "MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY": "rate",
        "MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY": "csrf",
        "MINTFLOW_AUTHENTICATION_WEB_ORIGIN": "https://app.mintflow.test",
        "MINTFLOW_AUTHENTICATION_RETURN_TARGETS": '["dashboard"]',
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        exit_code = telegram_retention_cleanup.main(["--batch-size", "10"])
    finally:
        get_settings.cache_clear()

    assert exit_code == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts["processed_updates_deleted"] == 1
    assert set(counts) == {
        "processed_updates_deleted",
        "link_challenges_deleted",
        "unlinked_connections_deleted",
        "drafts_expired",
        "receipts_cleared",
        "total",
    }


# --- RCPT-08: receipt images and recognition results -----------------------------------------

JPEG = b"\xff\xd8\xff\xe0receipt"


def _receipt_draft(
    session: Session,
    owner_id: UUID,
    *,
    state: CaptureDraftState,
    finished_at: datetime,
    receipt_state: str = "recognized",
) -> tuple[UUID, UUID]:
    """A receipt with its image, a recognition result, and a draft.

    Returns the receipt id and the draft id. A finished ``state`` is reached at
    ``finished_at``; otherwise the draft is left ready for review.
    """
    received = finished_at - DAY
    receipts = SqlAlchemyReceiptRepository(session)
    drafts = SqlAlchemyCaptureDraftRepository(session)
    receipt = Receipt.receive(owner_id=owner_id, now=received)
    attempt = receipt.claim(now=received, lease=timedelta(minutes=2))
    assert attempt.attempt_id is not None
    stored = (
        attempt
        if receipt_state == "processing"
        else attempt.complete(attempt_id=attempt.attempt_id, now=received)
    )
    receipts.create(stored, telegram_file_id="telegram-file")
    SqlAlchemyReceiptImageStore(session).put(
        receipt_id=receipt.id, media_type="image/jpeg", content=JPEG, now=received
    )
    result = RecognitionResult.for_attempt(
        receipt=receipt,
        attempt_id=attempt.attempt_id,
        merchant=None,
        transaction_date=TransactionDate(received.date()),
        total=Money(minor_units=1250, currency=CurrencyCode("EUR")),
        now=received,
    )
    receipts.save_result(result)
    draft = CaptureDraft.start_from_receipt(owner_id=owner_id, receipt_id=receipt.id, now=received)
    draft = draft.apply_recognition(
        result=result, fallback_category_key=UNCATEGORIZED_KEY, now=received
    )
    drafts.create(draft)
    if state is CaptureDraftState.CONFIRMED:
        ConfirmCaptureDraft(
            draft_repository=drafts,
            expense_repository=SqlAlchemyExpenseRepository(session),
            user_repository=SqlAlchemyUserRepository(session),
            clock=lambda: finished_at,
        ).execute(draft_id=draft.id, caller_id=owner_id)
    elif state is CaptureDraftState.CANCELLED:
        drafts.update(draft.cancel(caller_id=owner_id, now=finished_at))
    elif state is CaptureDraftState.EXPIRED:
        drafts.update(draft.expire(now=finished_at))
    # Open states: the draft stays ready for review; callers adjust it directly.
    session.commit()
    return receipt.id, draft.id


def _has_receipt_data(session: Session, receipt_id: UUID) -> bool:
    session.expire_all()
    images = session.scalar(
        select(func.count())
        .select_from(ReceiptImageRecord)
        .where(ReceiptImageRecord.receipt_id == receipt_id)
    )
    results = session.scalar(
        select(func.count())
        .select_from(RecognitionResultRecord)
        .where(RecognitionResultRecord.receipt_id == receipt_id)
    )
    assert images == results
    return bool(images)


@pytest.mark.parametrize(
    "state",
    [CaptureDraftState.CONFIRMED, CaptureDraftState.CANCELLED, CaptureDraftState.EXPIRED],
)
def test_receipt_data_is_kept_for_exactly_30_days_after_the_draft_finished(
    db_session: Session, state: CaptureDraftState
) -> None:
    user_id = _user(db_session)
    due, _ = _receipt_draft(db_session, user_id, state=state, finished_at=NOW - 30 * DAY)
    kept, _ = _receipt_draft(db_session, user_id, state=state, finished_at=NOW - 30 * DAY + SECOND)

    assert _cleanup(db_session)["receipts_cleared"] == 1

    assert not _has_receipt_data(db_session, due)
    assert _has_receipt_data(db_session, kept)
    removed = db_session.get(ReceiptRecord, due)
    assert removed is not None
    assert (removed.image_removed_at, removed.telegram_file_id) == (NOW, None)
    untouched = db_session.get(ReceiptRecord, kept)
    assert untouched is not None and untouched.image_removed_at is None


def test_expenses_keep_their_receipt_and_values(db_session: Session) -> None:
    user_id = _user(db_session)
    receipt_id, draft_id = _receipt_draft(
        db_session, user_id, state=CaptureDraftState.CONFIRMED, finished_at=NOW - 40 * DAY
    )
    before = db_session.scalars(select(ExpenseRecord)).one()
    snapshot = (before.id, before.receipt_id, before.amount_minor_units, before.transaction_date)
    db_session.commit()

    _cleanup(db_session)

    db_session.expire_all()
    after = db_session.scalars(select(ExpenseRecord)).one()
    assert (after.id, after.receipt_id, after.amount_minor_units, after.transaction_date) == (
        snapshot
    )
    assert after.receipt_id == receipt_id
    draft = db_session.get(CaptureDraftRecord, draft_id)
    assert draft is not None
    assert (draft.state, draft.receipt_id, draft.recognition_result_id) == (
        "confirmed",
        receipt_id,
        None,
    )


@pytest.mark.parametrize(
    "state",
    [
        CaptureDraftState.AWAITING_RECOGNITION,
        CaptureDraftState.COLLECTING,
        CaptureDraftState.READY_FOR_REVIEW,
    ],
)
def test_open_drafts_keep_their_receipt_data(db_session: Session, state: CaptureDraftState) -> None:
    user_id = _user(db_session)
    receipt_id, draft_id = _receipt_draft(
        db_session, user_id, state=state, finished_at=NOW - 60 * DAY
    )
    values: dict[str, object] = {"state": state.value, "modified_at": NOW - 60 * DAY}
    if state is CaptureDraftState.AWAITING_RECOGNITION:
        values["recognition_result_id"] = None
    db_session.execute(
        update(CaptureDraftRecord).where(CaptureDraftRecord.id == draft_id).values(**values)
    )
    db_session.commit()

    counts = _cleanup(db_session)

    # The abandoned draft expires now, so its 30 days only start today.
    assert (counts["drafts_expired"], counts["receipts_cleared"]) == (1, 0)
    assert _state(db_session, draft_id) == CaptureDraftState.EXPIRED.value
    assert _has_receipt_data(db_session, receipt_id)


def test_a_receipt_being_processed_is_skipped(db_session: Session) -> None:
    user_id = _user(db_session)
    receipt_id, _ = _receipt_draft(
        db_session,
        user_id,
        state=CaptureDraftState.CANCELLED,
        finished_at=NOW - 40 * DAY,
        receipt_state="processing",
    )

    assert _cleanup(db_session)["receipts_cleared"] == 0
    assert _has_receipt_data(db_session, receipt_id)


def test_receipt_batches_are_bounded_and_reruns_find_nothing(db_session: Session) -> None:
    user_id = _user(db_session)
    for _ in range(3):
        _receipt_draft(
            db_session, user_id, state=CaptureDraftState.CANCELLED, finished_at=NOW - 40 * DAY
        )

    counts = [_cleanup(db_session, batch_size=2)["receipts_cleared"] for _ in range(3)]

    assert counts == [2, 1, 0]
    assert db_session.scalar(select(func.count()).select_from(ReceiptImageRecord)) == 0


def test_concurrent_runs_clear_each_receipt_once(engine: Engine) -> None:
    with Session(engine) as setup:
        user_id = _user(setup)
        for _ in range(10):
            _receipt_draft(
                setup, user_id, state=CaptureDraftState.EXPIRED, finished_at=NOW - 40 * DAY
            )
    barrier = Barrier(2)

    def run() -> int:
        with Session(engine) as session:
            service = CleanUpTelegramRetention(
                repository=PostgreSQLTelegramRetentionRepository(session), clock=lambda: NOW
            )
            barrier.wait(timeout=5)
            return service.execute(batch_size=10).receipts_cleared

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: run(), range(2)))

    assert sum(counts) == 10
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ReceiptImageRecord)) == 0
        assert session.scalar(select(func.count()).select_from(RecognitionResultRecord)) == 0
        assert set(session.scalars(select(ReceiptRecord.image_removed_at))) == {NOW}


def test_expiry_releases_a_conversation_holding_a_pending_receipt(db_session: Session) -> None:
    user_id = _user(db_session)
    abandoned = _draft(db_session, user_id, modified_at=NOW - 8 * DAY)
    db_session.add(
        TelegramConversationRecord(
            user_id=user_id,
            active_draft_id=abandoned.id,
            awaiting="amount",
            currency_is_default=False,
            pending_receipt_file_id="held-photo",
            updated_at=NOW - 8 * DAY,
        )
    )
    db_session.commit()

    assert _cleanup(db_session)["drafts_expired"] == 1

    conversation = db_session.get(TelegramConversationRecord, user_id)
    assert conversation is not None
    assert (conversation.active_draft_id, conversation.pending_receipt_file_id) == (None, None)
