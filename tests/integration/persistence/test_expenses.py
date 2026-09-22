from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import ExpenseRecord, UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _add_user(session: Session) -> UserRecord:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    return user


def _add_draft(session: Session, owner_id: UUID) -> CaptureDraft:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    return draft


def _expense_for(draft: CaptureDraft) -> Expense:
    return Expense.create(
        owner_id=draft.owner_id,
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 4)),
        category_key="groceries",
        capture_draft_id=draft.id,
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )


def test_create_and_scoped_get_round_trip(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = _add_draft(db_session, user.id)
    other_owner = uuid4()
    expense = _expense_for(draft)
    repository = SqlAlchemyExpenseRepository(db_session)

    repository.create(expense)

    assert repository.get(expense_id=expense.id, owner_id=user.id) == expense
    assert repository.get(expense_id=expense.id, owner_id=other_owner) is None


def test_get_by_draft_id_positive_and_negative(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = _add_draft(db_session, user.id)
    expense = _expense_for(draft)
    repository = SqlAlchemyExpenseRepository(db_session)
    repository.create(expense)

    assert repository.get_by_draft_id(capture_draft_id=draft.id) == expense
    assert repository.get_by_draft_id(capture_draft_id=uuid4()) is None


def test_soft_delete_and_restore_round_trip_through_repository(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = _add_draft(db_session, user.id)
    expense = _expense_for(draft)
    repository = SqlAlchemyExpenseRepository(db_session)
    repository.create(expense)

    deleted = expense.delete(now=NOW + timedelta(minutes=1))
    repository.update(deleted)
    fetched = repository.get(expense_id=expense.id, owner_id=user.id)
    assert fetched is not None
    assert fetched.is_active is False
    assert fetched.deleted_at == NOW + timedelta(minutes=1)

    restored = fetched.restore(now=NOW + timedelta(minutes=2))
    repository.update(restored)
    fetched_again = repository.get(expense_id=expense.id, owner_id=user.id)
    assert fetched_again is not None
    assert fetched_again.is_active is True
    assert fetched_again.deleted_at is None


def test_amount_must_be_positive_at_the_database_level(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = _add_draft(db_session, user.id)
    record = ExpenseRecord(
        id=uuid4(),
        owner_id=user.id,
        amount_minor_units=0,
        amount_currency="USD",
        transaction_date=date(2026, 8, 4),
        category_key="groceries",
        source=CaptureSource.WEB_MANUAL.value,
        capture_draft_id=draft.id,
        created_at=NOW,
        modified_at=NOW,
    )
    db_session.add(record)

    with pytest.raises(IntegrityError, match="ck_expenses_amount_positive"):
        db_session.commit()
    db_session.rollback()


def test_duplicate_draft_id_is_rejected_by_the_unique_constraint(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = _add_draft(db_session, user.id)
    repository = SqlAlchemyExpenseRepository(db_session)
    repository.create(_expense_for(draft))

    with pytest.raises(IntegrityError, match="uq_expenses_capture_draft_id"):
        repository.create(_expense_for(draft))
    db_session.rollback()


def test_concurrent_inserts_for_the_same_draft_allow_exactly_one_winner(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    draft = _add_draft(db_session, user.id)

    engine_a = create_database_engine(migrated_database_url)
    engine_b = create_database_engine(migrated_database_url)
    session_a = create_session_factory(engine_a)()
    session_b = create_session_factory(engine_b)()
    barrier = Barrier(2)

    def worker(session: Session) -> Exception | None:
        try:
            barrier.wait(timeout=5)
            SqlAlchemyExpenseRepository(session).create(_expense_for(draft))
            return None
        except Exception as error:  # noqa: BLE001 - capturing to assert exactly one winner
            session.rollback()
            return error

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, [session_a, session_b]))
    finally:
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()

    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, IntegrityError) for result in results) == 1
    db_session.expire_all()
    assert (
        SqlAlchemyExpenseRepository(db_session).get_by_draft_id(capture_draft_id=draft.id)
        is not None
    )
