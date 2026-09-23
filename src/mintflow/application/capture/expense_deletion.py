from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from mintflow.application.capture.edit_expense import ExpenseNotFound
from mintflow.application.capture.expense_changes import (
    ExpenseChangeRecord,
    ExpenseChangeRecordAppender,
    ExpenseChangeType,
)
from mintflow.domain.capture import Expense


class OwnedExpenseRepository(Protocol):
    def get_owned_for_update(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None: ...

    def update(self, expense: Expense, *, commit: bool = True) -> None: ...


class _DeletionStateChange:
    """Lock an owned Expense in any deletion state, move it to the target state, record it.

    Repeating an operation already in effect is a successful no-op that writes
    nothing, so delete and restore are safe to retry. Concurrent requests
    serialize on the row lock, so exactly one of them records the change.
    """

    _change_type: ExpenseChangeType

    def __init__(
        self,
        *,
        expense_repository: OwnedExpenseRepository,
        change_records: ExpenseChangeRecordAppender,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._expense_repository = expense_repository
        self._change_records = change_records
        self._clock = clock

    def execute(self, *, expense_id: UUID, caller_id: UUID) -> Expense:
        expense = self._expense_repository.get_owned_for_update(
            expense_id=expense_id, owner_id=caller_id
        )
        if expense is None:
            raise ExpenseNotFound("expense not found or not owned by caller")
        if self._already_in_effect(expense):
            return expense

        now = self._clock()
        changed = self._transition(expense, now=now)
        self._change_records.append(
            ExpenseChangeRecord(
                expense_id=expense.id,
                actor_user_id=caller_id,
                occurred_at=now,
                change_type=self._change_type,
            )
        )
        self._expense_repository.update(changed, commit=True)
        return changed

    def _already_in_effect(self, expense: Expense) -> bool:
        raise NotImplementedError

    def _transition(self, expense: Expense, *, now: datetime) -> Expense:
        raise NotImplementedError


class DeleteExpense(_DeletionStateChange):
    """Soft-delete a confirmed Expense (product decision 6)."""

    _change_type = ExpenseChangeType.DELETED

    def _already_in_effect(self, expense: Expense) -> bool:
        return not expense.is_active

    def _transition(self, expense: Expense, *, now: datetime) -> Expense:
        return expense.delete(now=now)


class RestoreExpense(_DeletionStateChange):
    """Return a soft-deleted Expense to financial history, unchanged except ``modified_at``."""

    _change_type = ExpenseChangeType.RESTORED

    def _already_in_effect(self, expense: Expense) -> bool:
        return expense.is_active

    def _transition(self, expense: Expense, *, now: datetime) -> Expense:
        return expense.restore(now=now)
