import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.telegram.retention import CleanUpTelegramRetention
from mintflow.commands import telegram_retention_cleanup
from mintflow.config import get_settings
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureDraftState,
    CaptureSource,
    CurrencyCode,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import SqlAlchemyCaptureDraftRepository
from mintflow.infrastructure.persistence.models import (
    CaptureDraftRecord,
    ExpenseRecord,
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
        "total",
    }
