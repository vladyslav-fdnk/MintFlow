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
    EditExpense,
    ExpenseChangeRecord,
    ExpenseChangeRecordAppender,
    ExpenseEdit,
    ExpenseNotFound,
)
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseChangeRecordAppender,
    SqlAlchemyExpenseRepository,
    SqlAlchemyUserRepository,
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
        merchant=MerchantName("Corner Shop"),
        note="milk",
        source=CaptureSource.WEB_MANUAL,
        now=CREATED,
    )
    SqlAlchemyExpenseRepository(session).create(expense)
    return expense


def _use_case(
    session: Session,
    *,
    clock: Callable[[], datetime] = lambda: NOW,
    change_records: ExpenseChangeRecordAppender | None = None,
) -> EditExpense:
    return EditExpense(
        expense_repository=SqlAlchemyExpenseRepository(session),
        change_records=change_records or SqlAlchemyExpenseChangeRecordAppender(session),
        category_repository=SqlAlchemyCategoryRepository(session),
        user_repository=SqlAlchemyUserRepository(session),
        clock=clock,
    )


def _records(session: Session, expense_id: UUID) -> list[ExpenseChangeRecordModel]:
    return list(
        session.scalars(
            select(ExpenseChangeRecordModel)
            .where(ExpenseChangeRecordModel.expense_id == expense_id)
            .order_by(ExpenseChangeRecordModel.occurred_at)
        ).all()
    )


def test_edit_persists_the_expense_and_its_change_record_together(db_session: Session) -> None:
    expense = _add_expense(db_session)

    edited = _use_case(db_session).execute(
        expense_id=expense.id,
        caller_id=expense.owner_id,
        edit=ExpenseEdit(
            money=Money(minor_units=1750, currency=CurrencyCode("USD")),
            merchant=None,
            category_key="food_and_dining",
        ),
    )

    db_session.expire_all()
    stored = SqlAlchemyExpenseRepository(db_session).get_active(
        expense_id=expense.id, owner_id=expense.owner_id
    )
    assert stored is not None
    assert stored == edited
    assert stored.modified_at == NOW
    [record] = _records(db_session, expense.id)
    assert record.change_type == "edited"
    assert record.actor_user_id == expense.owner_id
    assert record.occurred_at == NOW
    assert record.changes == {
        "amount_minor_units": {"old": 1500, "new": 1750},
        "merchant": {"old": "Corner Shop", "new": None},
        "category_key": {"old": "groceries", "new": "food_and_dining"},
    }


def test_no_op_edit_writes_nothing(db_session: Session) -> None:
    expense = _add_expense(db_session)

    _use_case(db_session).execute(
        expense_id=expense.id, caller_id=expense.owner_id, edit=ExpenseEdit(note="milk")
    )
    db_session.commit()

    db_session.expire_all()
    stored = SqlAlchemyExpenseRepository(db_session).get(
        expense_id=expense.id, owner_id=expense.owner_id
    )
    assert stored == expense
    assert _records(db_session, expense.id) == []


def test_deleted_and_foreign_expenses_are_not_found(db_session: Session) -> None:
    expense = _add_expense(db_session)
    other = _add_expense(db_session)
    SqlAlchemyExpenseRepository(db_session).update(expense.delete(now=CREATED))

    with pytest.raises(ExpenseNotFound):
        _use_case(db_session).execute(
            expense_id=expense.id, caller_id=expense.owner_id, edit=ExpenseEdit(note="x")
        )
    db_session.rollback()
    with pytest.raises(ExpenseNotFound):
        _use_case(db_session).execute(
            expense_id=other.id, caller_id=expense.owner_id, edit=ExpenseEdit(note="x")
        )
    db_session.rollback()

    assert _records(db_session, expense.id) == []
    assert _records(db_session, other.id) == []


class FailingAppender:
    """Writes a record the database must reject, as a stand-in for any insert failure."""

    def __init__(self, session: Session) -> None:
        self._appender = SqlAlchemyExpenseChangeRecordAppender(session)

    def append(self, record: ExpenseChangeRecord) -> None:
        self._appender.append(replace(record, actor_user_id=uuid4()))


def test_failed_change_record_leaves_the_expense_unchanged(db_session: Session) -> None:
    expense = _add_expense(db_session)

    with pytest.raises(IntegrityError):
        _use_case(db_session, change_records=FailingAppender(db_session)).execute(
            expense_id=expense.id, caller_id=expense.owner_id, edit=ExpenseEdit(note="changed")
        )
    db_session.rollback()

    stored = SqlAlchemyExpenseRepository(db_session).get(
        expense_id=expense.id, owner_id=expense.owner_id
    )
    assert stored == expense
    assert _records(db_session, expense.id) == []


def test_concurrent_edits_serialize_and_their_records_chain(
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

    def worker_a() -> Expense:
        return _use_case(session_a, clock=blocking_clock).execute(
            expense_id=expense.id, caller_id=expense.owner_id, edit=ExpenseEdit(note="first")
        )

    def worker_b() -> Expense:
        return _use_case(session_b, clock=lambda: NOW.replace(minute=1)).execute(
            expense_id=expense.id, caller_id=expense.owner_id, edit=ExpenseEdit(note="second")
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(worker_a)
            assert started.wait(timeout=5)
            future_b = executor.submit(worker_b)
            # B must be blocked on A's row lock, not racing ahead.
            with pytest.raises(TimeoutError):
                future_b.result(timeout=0.3)
            release.set()
            future_a.result(timeout=5)
            result_b = future_b.result(timeout=5)
    finally:
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()

    assert result_b.note == "second"
    db_session.expire_all()
    records = _records(db_session, expense.id)
    assert [record.changes for record in records] == [
        {"note": {"old": "milk", "new": "first"}},
        {"note": {"old": "first", "new": "second"}},
    ]
