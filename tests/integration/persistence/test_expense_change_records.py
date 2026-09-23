from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.capture import (
    ExpenseChangeRecord,
    ExpenseChangeType,
    ExpenseField,
    ExpenseFieldChange,
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
)
from mintflow.infrastructure.persistence.models import (
    CaptureDraftRecord,
    ExpenseChangeRecordModel,
    ExpenseRecord,
    UserRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _add_expense(session: Session) -> Expense:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    expense = Expense.create(
        owner_id=user.id,
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 4)),
        category_key="groceries",
        capture_draft_id=draft.id,
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )
    SqlAlchemyExpenseRepository(session).create(expense)
    return expense


def _edited(expense: Expense) -> ExpenseChangeRecord:
    return ExpenseChangeRecord(
        expense_id=expense.id,
        actor_user_id=expense.owner_id,
        occurred_at=NOW + timedelta(minutes=1),
        change_type=ExpenseChangeType.EDITED,
        changes=(
            ExpenseFieldChange(ExpenseField.AMOUNT_MINOR_UNITS, 1500, 1750),
            ExpenseFieldChange(ExpenseField.NOTE, None, "coffee"),
        ),
    )


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ExpenseChangeRecordModel)) or 0


def _insert_raw(
    session: Session, expense: Expense, *, change_type: str, changes: str | None
) -> None:
    session.execute(
        text(
            "INSERT INTO expense_change_records "
            "(id, expense_id, actor_user_id, occurred_at, change_type, changes) "
            "VALUES (:id, :expense_id, :actor, :occurred_at, :change_type, "
            "CAST(:changes AS jsonb))"
        ),
        {
            "id": uuid4(),
            "expense_id": expense.id,
            "actor": expense.owner_id,
            "occurred_at": NOW,
            "change_type": change_type,
            "changes": changes,
        },
    )


def test_appended_records_persist_with_their_changes_document(db_session: Session) -> None:
    expense = _add_expense(db_session)
    edited = _edited(expense)
    deleted = ExpenseChangeRecord(
        expense_id=expense.id,
        actor_user_id=expense.owner_id,
        occurred_at=NOW + timedelta(minutes=2),
        change_type=ExpenseChangeType.DELETED,
    )
    appender = SqlAlchemyExpenseChangeRecordAppender(db_session)

    appender.append(edited)
    appender.append(deleted)
    db_session.commit()

    rows = db_session.scalars(
        select(ExpenseChangeRecordModel).order_by(ExpenseChangeRecordModel.occurred_at)
    ).all()
    assert [(row.id, row.change_type) for row in rows] == [
        (edited.id, "edited"),
        (deleted.id, "deleted"),
    ]
    assert rows[0].changes == {
        "amount_minor_units": {"old": 1500, "new": 1750},
        "note": {"old": None, "new": "coffee"},
    }
    assert rows[0].actor_user_id == expense.owner_id
    assert rows[0].occurred_at == NOW + timedelta(minutes=1)
    # SQL NULL, not a JSON null document.
    assert (
        db_session.scalar(
            text("SELECT changes IS NULL FROM expense_change_records WHERE id = :id"),
            {"id": deleted.id},
        )
        is True
    )


def test_append_does_not_commit_and_rolls_back_with_the_caller(db_session: Session) -> None:
    expense = _add_expense(db_session)
    appender = SqlAlchemyExpenseChangeRecordAppender(db_session)

    appender.append(_edited(expense))
    assert _count(db_session) == 1
    db_session.rollback()

    assert _count(db_session) == 0


def test_a_failed_append_leaves_no_partial_commit_of_the_surrounding_change(
    db_session: Session,
) -> None:
    expense = _add_expense(db_session)
    appender = SqlAlchemyExpenseChangeRecordAppender(db_session)
    db_session.execute(
        update(ExpenseRecord).where(ExpenseRecord.id == expense.id).values(note="changed")
    )
    orphan = ExpenseChangeRecord(
        expense_id=uuid4(),  # violates the Expense foreign key
        actor_user_id=expense.owner_id,
        occurred_at=NOW,
        change_type=ExpenseChangeType.DELETED,
    )

    with pytest.raises(IntegrityError):
        appender.append(orphan)
    db_session.rollback()

    assert (
        db_session.scalar(select(ExpenseRecord.note).where(ExpenseRecord.id == expense.id)) is None
    )
    assert _count(db_session) == 0


@pytest.mark.parametrize(
    ("change_type", "changes"),
    [
        pytest.param("edited", None, id="edited without changes"),
        pytest.param("edited", "{}", id="edited with empty changes"),
        pytest.param("edited", "[1]", id="edited with non-object changes"),
        pytest.param("deleted", '{"note": {"old": null, "new": "x"}}', id="deleted with changes"),
        pytest.param("restored", '{"note": {"old": null, "new": "x"}}', id="restored with changes"),
        pytest.param("renamed", None, id="unknown change type"),
    ],
)
def test_database_constraints_reject_inconsistent_records(
    db_session: Session, change_type: str, changes: str | None
) -> None:
    expense = _add_expense(db_session)

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, expense, change_type=change_type, changes=changes)
    db_session.rollback()


def test_actor_must_be_an_existing_user(db_session: Session) -> None:
    expense = _add_expense(db_session)
    record = ExpenseChangeRecord(
        expense_id=expense.id,
        actor_user_id=uuid4(),
        occurred_at=NOW,
        change_type=ExpenseChangeType.RESTORED,
    )

    with pytest.raises(IntegrityError):
        SqlAlchemyExpenseChangeRecordAppender(db_session).append(record)
    db_session.rollback()


def test_deleting_the_expense_row_cascades_to_its_records(db_session: Session) -> None:
    expense = _add_expense(db_session)
    other = _add_expense(db_session)
    appender = SqlAlchemyExpenseChangeRecordAppender(db_session)
    appender.append(_edited(expense))
    appender.append(_edited(other))
    db_session.commit()

    # The draft references the Expense; detach it first, as a purge would.
    db_session.execute(
        update(CaptureDraftRecord)
        .where(CaptureDraftRecord.id == expense.capture_draft_id)
        .values(expense_id=None)
    )
    db_session.execute(delete(ExpenseRecord).where(ExpenseRecord.id == expense.id))
    db_session.commit()

    remaining: list[UUID] = list(
        db_session.scalars(select(ExpenseChangeRecordModel.expense_id)).all()
    )
    assert remaining == [other.id]
