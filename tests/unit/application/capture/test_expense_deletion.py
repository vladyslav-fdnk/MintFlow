from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from mintflow.application.capture import (
    DeleteExpense,
    ExpenseChangeRecord,
    ExpenseChangeType,
    ExpenseNotFound,
    RestoreExpense,
)
from mintflow.domain.capture import CaptureSource, CurrencyCode, Expense, Money, TransactionDate

OWNER = uuid4()
CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _expense() -> Expense:
    return Expense.create(
        owner_id=OWNER,
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 4)),
        category_key="groceries",
        capture_draft_id=uuid4(),
        source=CaptureSource.WEB_MANUAL,
        now=CREATED,
    )


class FakeExpenseRepository:
    def __init__(self, expense: Expense) -> None:
        self.expense = expense
        self.updates: list[tuple[Expense, bool]] = []

    def get_owned_for_update(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        if self.expense.id != expense_id or self.expense.owner_id != owner_id:
            return None
        return self.expense

    def update(self, expense: Expense, *, commit: bool = True) -> None:
        self.updates.append((expense, commit))
        self.expense = expense


class FakeChangeRecords:
    def __init__(self) -> None:
        self.records: list[ExpenseChangeRecord] = []

    def append(self, record: ExpenseChangeRecord) -> None:
        self.records.append(record)


def _wire(
    use_case_type: type[DeleteExpense] | type[RestoreExpense], expense: Expense
) -> tuple[DeleteExpense | RestoreExpense, FakeExpenseRepository, FakeChangeRecords]:
    repository = FakeExpenseRepository(expense)
    records = FakeChangeRecords()
    use_case = use_case_type(
        expense_repository=repository, change_records=records, clock=lambda: NOW
    )
    return use_case, repository, records


def test_delete_marks_the_expense_deleted_and_records_it() -> None:
    expense = _expense()
    use_case, repository, records = _wire(DeleteExpense, expense)

    deleted = use_case.execute(expense_id=expense.id, caller_id=OWNER)

    assert deleted.deleted_at == NOW
    assert deleted.modified_at == NOW
    assert repository.updates == [(deleted, True)]
    [record] = records.records
    assert record.change_type is ExpenseChangeType.DELETED
    assert (record.expense_id, record.actor_user_id, record.occurred_at) == (expense.id, OWNER, NOW)
    assert record.changes == ()


def test_restore_returns_the_expense_to_history_and_records_it() -> None:
    expense = _expense().delete(now=CREATED)
    use_case, repository, records = _wire(RestoreExpense, expense)

    restored = use_case.execute(expense_id=expense.id, caller_id=OWNER)

    assert restored.is_active
    assert restored.modified_at == NOW
    assert restored.money == expense.money
    assert repository.updates == [(restored, True)]
    [record] = records.records
    assert record.change_type is ExpenseChangeType.RESTORED


@pytest.mark.parametrize(
    ("use_case_type", "expense"),
    [
        pytest.param(DeleteExpense, _expense().delete(now=CREATED), id="delete already deleted"),
        pytest.param(RestoreExpense, _expense(), id="restore already active"),
    ],
)
def test_repeating_an_operation_in_effect_is_a_no_op(
    use_case_type: type[DeleteExpense] | type[RestoreExpense], expense: Expense
) -> None:
    use_case, repository, records = _wire(use_case_type, expense)

    result = use_case.execute(expense_id=expense.id, caller_id=OWNER)

    assert result == expense
    assert repository.updates == []
    assert records.records == []


@pytest.mark.parametrize("use_case_type", [DeleteExpense, RestoreExpense])
def test_missing_or_foreign_expenses_are_not_found(
    use_case_type: type[DeleteExpense] | type[RestoreExpense],
) -> None:
    expense = _expense()
    use_case, repository, records = _wire(use_case_type, expense)

    with pytest.raises(ExpenseNotFound):
        use_case.execute(expense_id=expense.id, caller_id=uuid4())
    with pytest.raises(ExpenseNotFound):
        use_case.execute(expense_id=uuid4(), caller_id=OWNER)
    assert repository.updates == []
    assert records.records == []
