from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mintflow.application.capture import (
    CaptureDraftAccessDenied,
    CaptureDraftNotConfirmable,
    ConfirmCaptureDraft,
)
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyUserRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import CaptureDraftRecord, ExpenseRecord, UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
MONEY = Money(minor_units=1500, currency=CurrencyCode("USD"))
TX_DATE = TransactionDate(date(2026, 8, 6))


def _add_user(session: Session) -> UserRecord:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    return user


def _add_confirmable_draft(
    session: Session, owner_id: UUID, **field_overrides: object
) -> CaptureDraft:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    draft = draft.set_amount(
        caller_id=owner_id,
        amount=field_overrides.get("amount", MONEY),  # type: ignore[arg-type]
        now=NOW,
    )
    draft = draft.set_transaction_date(
        caller_id=owner_id,
        transaction_date=field_overrides.get("transaction_date", TX_DATE),  # type: ignore[arg-type]
        now=NOW,
    )
    draft = draft.mark_ready_for_review(caller_id=owner_id, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    return draft


def _use_case(session: Session, *, clock: datetime = NOW) -> ConfirmCaptureDraft:
    return ConfirmCaptureDraft(
        draft_repository=SqlAlchemyCaptureDraftRepository(session),
        expense_repository=SqlAlchemyExpenseRepository(session),
        user_repository=SqlAlchemyUserRepository(session),
        clock=lambda: clock,
    )


def test_confirms_a_confirmable_draft_end_to_end(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = _add_confirmable_draft(db_session, user.id)
    use_case = _use_case(db_session)

    expense = use_case.execute(draft_id=draft.id, caller_id=user.id)

    db_session.expire_all()
    expense_row = db_session.get(ExpenseRecord, expense.id)
    assert expense_row is not None
    assert expense_row.capture_draft_id == draft.id
    draft_row = db_session.get(CaptureDraftRecord, draft.id)
    assert draft_row is not None
    assert draft_row.state == "confirmed"
    assert draft_row.expense_id == expense.id


def test_duplicate_confirmation_returns_the_same_expense_with_no_additional_row(
    db_session: Session,
) -> None:
    user = _add_user(db_session)
    draft = _add_confirmable_draft(db_session, user.id)
    use_case = _use_case(db_session)

    first = use_case.execute(draft_id=draft.id, caller_id=user.id)
    second = use_case.execute(draft_id=draft.id, caller_id=user.id)

    assert first.id == second.id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ExpenseRecord)
            .where(ExpenseRecord.capture_draft_id == draft.id)
        )
        == 1
    )


@pytest.mark.parametrize("build", ["not_confirmable", "cancelled", "expired"])
def test_rejects_drafts_that_cannot_be_confirmed_and_creates_no_expense(
    db_session: Session, build: str
) -> None:
    user = _add_user(db_session)
    if build == "not_confirmable":
        draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.WEB_MANUAL, now=NOW)
        SqlAlchemyCaptureDraftRepository(db_session).create(draft)
    else:
        draft = _add_confirmable_draft(db_session, user.id)
        transitioned = (
            draft.cancel(caller_id=user.id, now=NOW)
            if build == "cancelled"
            else draft.expire(now=NOW)
        )
        SqlAlchemyCaptureDraftRepository(db_session).update(transitioned)
    use_case = _use_case(db_session)

    with pytest.raises(CaptureDraftNotConfirmable):
        use_case.execute(draft_id=draft.id, caller_id=user.id)

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ExpenseRecord)
            .where(ExpenseRecord.capture_draft_id == draft.id)
        )
        == 0
    )


def test_rejects_a_non_owner_caller(db_session: Session) -> None:
    user = _add_user(db_session)
    stranger = _add_user(db_session)
    draft = _add_confirmable_draft(db_session, user.id)
    use_case = _use_case(db_session)

    with pytest.raises(CaptureDraftAccessDenied):
        use_case.execute(draft_id=draft.id, caller_id=stranger.id)


def test_future_date_more_than_one_day_ahead_is_rejected_with_injected_clock(
    db_session: Session,
) -> None:
    user = _add_user(db_session)
    draft = _add_confirmable_draft(
        db_session, user.id, transaction_date=TransactionDate(NOW.date() + timedelta(days=2))
    )
    use_case = _use_case(db_session, clock=NOW)

    with pytest.raises(CaptureDraftNotConfirmable, match="future"):
        use_case.execute(draft_id=draft.id, caller_id=user.id)


def test_concurrent_confirmation_attempts_produce_exactly_one_expense(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    draft = _add_confirmable_draft(db_session, user.id)

    engine_a = create_database_engine(migrated_database_url)
    engine_b = create_database_engine(migrated_database_url)
    session_a = create_session_factory(engine_a)()
    session_b = create_session_factory(engine_b)()
    started = Event()
    release = Event()

    def blocking_clock() -> datetime:
        # Called once execute() has already acquired the row lock (right
        # after get_for_update) and validated confirmability, but before any
        # write. A perfect, already-existing synchronization point -- no
        # monkeypatching of the repository needed.
        started.set()
        assert release.wait(timeout=5)
        return NOW

    def worker_a() -> object:
        use_case = ConfirmCaptureDraft(
            draft_repository=SqlAlchemyCaptureDraftRepository(session_a),
            expense_repository=SqlAlchemyExpenseRepository(session_a),
            user_repository=SqlAlchemyUserRepository(session_a),
            clock=blocking_clock,
        )
        return use_case.execute(draft_id=draft.id, caller_id=user.id)

    def worker_b() -> object:
        assert started.wait(timeout=5)
        use_case = _use_case(session_b)
        return use_case.execute(draft_id=draft.id, caller_id=user.id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(worker_a)
            assert started.wait(timeout=5)
            future_b = executor.submit(worker_b)
            with pytest.raises(TimeoutError):
                future_b.result(timeout=0.3)
            release.set()
            expense_a = future_a.result(timeout=5)
            expense_b = future_b.result(timeout=5)
    finally:
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()

    assert expense_a.id == expense_b.id  # type: ignore[attr-defined]
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ExpenseRecord)
            .where(ExpenseRecord.capture_draft_id == draft.id)
        )
        == 1
    )
