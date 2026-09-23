from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.capture import (
    DeleteExpense,
    ExpenseChangeRecord,
    ExpenseHistoryFilter,
    RestoreExpense,
)
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
    SqlAlchemyExpenseChangeRecordAppender,
    SqlAlchemyExpenseRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import ExpenseChangeRecordModel, UserRecord

pytestmark = pytest.mark.integration

CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _add_expense(session: Session) -> Expense:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=CREATED)
    session.add(user)
    session.commit()
    draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.WEB_MANUAL, now=CREATED)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    expense = Expense.create(
        owner_id=user.id,
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 4)),
        category_key="groceries",
        capture_draft_id=draft.id,
        source=CaptureSource.WEB_MANUAL,
        now=CREATED,
    )
    SqlAlchemyExpenseRepository(session).create(expense)
    return expense


def _change_types(session: Session, expense_id: UUID) -> list[str]:
    return list(
        session.scalars(
            select(ExpenseChangeRecordModel.change_type)
            .where(ExpenseChangeRecordModel.expense_id == expense_id)
            .order_by(ExpenseChangeRecordModel.occurred_at)
        ).all()
    )


def test_delete_hides_from_history_and_restore_brings_back(db_session: Session) -> None:
    expense = _add_expense(db_session)
    repository = SqlAlchemyExpenseRepository(db_session)
    appender = SqlAlchemyExpenseChangeRecordAppender(db_session)

    def history() -> list[UUID]:
        page = repository.list_history(
            owner_id=expense.owner_id, history_filter=ExpenseHistoryFilter(), limit=50
        )
        return [item.id for item in page.items]

    DeleteExpense(
        expense_repository=repository, change_records=appender, clock=lambda: NOW
    ).execute(expense_id=expense.id, caller_id=expense.owner_id)
    db_session.expire_all()
    assert history() == []
    assert repository.get_active(expense_id=expense.id, owner_id=expense.owner_id) is None

    restored = RestoreExpense(
        expense_repository=repository, change_records=appender, clock=lambda: NOW
    ).execute(expense_id=expense.id, caller_id=expense.owner_id)
    db_session.expire_all()
    assert history() == [expense.id]
    assert restored == replace(expense, modified_at=NOW)
    assert repository.get_active(expense_id=expense.id, owner_id=expense.owner_id) == restored
    assert _change_types(db_session, expense.id) == ["deleted", "restored"]


class FailingAppender:
    """Writes a record the database must reject, as a stand-in for any insert failure."""

    def __init__(self, session: Session) -> None:
        self._appender = SqlAlchemyExpenseChangeRecordAppender(session)

    def append(self, record: ExpenseChangeRecord) -> None:
        self._appender.append(replace(record, actor_user_id=uuid4()))


def test_failed_change_record_leaves_the_deletion_state_unchanged(db_session: Session) -> None:
    expense = _add_expense(db_session)
    repository = SqlAlchemyExpenseRepository(db_session)
    use_case = DeleteExpense(
        expense_repository=repository,
        change_records=FailingAppender(db_session),
        clock=lambda: NOW,
    )

    with pytest.raises(IntegrityError):
        use_case.execute(expense_id=expense.id, caller_id=expense.owner_id)
    db_session.rollback()

    assert repository.get_active(expense_id=expense.id, owner_id=expense.owner_id) == expense
    assert _change_types(db_session, expense.id) == []


def test_concurrent_deletes_record_exactly_one_deletion(
    db_session: Session, migrated_database_url: str
) -> None:
    expense = _add_expense(db_session)
    engine_a = create_database_engine(migrated_database_url)
    engine_b = create_database_engine(migrated_database_url)
    session_a = create_session_factory(engine_a)()
    session_b = create_session_factory(engine_b)()
    started = Event()
    release = Event()

    def blocking_clock() -> datetime:
        # Called only after execute() holds the row lock and before any write.
        started.set()
        assert release.wait(timeout=5)
        return NOW

    def delete_with(session: Session, clock: Callable[[], datetime]) -> Expense:
        return DeleteExpense(
            expense_repository=SqlAlchemyExpenseRepository(session),
            change_records=SqlAlchemyExpenseChangeRecordAppender(session),
            clock=clock,
        ).execute(expense_id=expense.id, caller_id=expense.owner_id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(delete_with, session_a, blocking_clock)
            assert started.wait(timeout=5)
            future_b = executor.submit(delete_with, session_b, lambda: NOW)
            with pytest.raises(TimeoutError):
                future_b.result(timeout=0.3)
            release.set()
            result_a = future_a.result(timeout=5)
            result_b = future_b.result(timeout=5)
    finally:
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()

    assert result_a.deleted_at == result_b.deleted_at == NOW
    db_session.expire_all()
    assert _change_types(db_session, expense.id) == ["deleted"]
