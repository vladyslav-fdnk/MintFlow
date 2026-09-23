from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Final, Protocol
from uuid import UUID

from mintflow.application.capture.expense_changes import (
    ExpenseChangeRecord,
    ExpenseChangeRecordAppender,
    ExpenseChangeType,
    ExpenseFieldChange,
    expense_field_values,
)
from mintflow.application.capture.transaction_dates import is_within_future_tolerance
from mintflow.domain.capture import Category, Expense, MerchantName, Money, TransactionDate
from mintflow.domain.user import User


class Unchanged(Enum):
    """Marks an edit-command field the caller did not supply.

    Distinct from ``None``, which explicitly clears an optional field.
    """

    UNCHANGED = "unchanged"


UNCHANGED: Final = Unchanged.UNCHANGED


@dataclass(frozen=True, slots=True)
class ExpenseEdit:
    money: Money | Unchanged = UNCHANGED
    transaction_date: TransactionDate | Unchanged = UNCHANGED
    merchant: MerchantName | None | Unchanged = UNCHANGED
    category_key: str | Unchanged = UNCHANGED
    note: str | None | Unchanged = UNCHANGED


class ExpenseNotFound(Exception):
    """No active Expense with this id is owned by the caller.

    Deliberately uniform across "does not exist", "owned by someone else",
    and "soft-deleted", like ``CaptureDraftAccessDenied``.
    """


class ExpenseEditRejected(Exception):
    """A supplied value is not acceptable for a confirmed Expense."""


class EditableExpenseRepository(Protocol):
    def get_active_for_update(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None: ...

    def update(self, expense: Expense, *, commit: bool = True) -> None: ...


class CategoryRepository(Protocol):
    def get_by_key(self, key: str) -> Category | None: ...


class UserRepository(Protocol):
    def get(self, user_id: UUID) -> User | None: ...


class EditExpense:
    """Correct a confirmed Expense and record exactly what changed, atomically.

    Locks the owner's active Expense, applies the domain edits, appends one
    ``edited`` change record listing only fields whose values changed, and
    commits both together. An edit that changes nothing writes nothing.
    Concurrent edits serialize on the row lock; the last one wins and each
    record's old values are the state it actually replaced (design D3).
    """

    def __init__(
        self,
        *,
        expense_repository: EditableExpenseRepository,
        change_records: ExpenseChangeRecordAppender,
        category_repository: CategoryRepository,
        user_repository: UserRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._expense_repository = expense_repository
        self._change_records = change_records
        self._category_repository = category_repository
        self._user_repository = user_repository
        self._clock = clock

    def execute(self, *, expense_id: UUID, caller_id: UUID, edit: ExpenseEdit) -> Expense:
        before = self._expense_repository.get_active_for_update(
            expense_id=expense_id, owner_id=caller_id
        )
        if before is None:
            raise ExpenseNotFound("expense not found, not owned by caller, or deleted")

        now = self._clock()
        after = self._apply(before, edit, caller_id=caller_id, now=now)

        old_values = expense_field_values(before)
        new_values = expense_field_values(after)
        changes = tuple(
            ExpenseFieldChange(field, old_values[field], new_values[field])
            for field in old_values
            if old_values[field] != new_values[field]
        )
        if not changes:
            return before

        self._change_records.append(
            ExpenseChangeRecord(
                expense_id=before.id,
                actor_user_id=caller_id,
                occurred_at=now,
                change_type=ExpenseChangeType.EDITED,
                changes=changes,
            )
        )
        self._expense_repository.update(after, commit=True)
        return after

    def _apply(
        self, expense: Expense, edit: ExpenseEdit, *, caller_id: UUID, now: datetime
    ) -> Expense:
        try:
            if not isinstance(edit.money, Unchanged):
                expense = expense.edit_money(money=edit.money, now=now)
            if not isinstance(edit.transaction_date, Unchanged):
                if edit.transaction_date != expense.transaction_date:
                    self._require_acceptable_date(
                        edit.transaction_date, caller_id=caller_id, now=now
                    )
                expense = expense.edit_transaction_date(
                    transaction_date=edit.transaction_date, now=now
                )
            if not isinstance(edit.merchant, Unchanged):
                expense = expense.edit_merchant(merchant=edit.merchant, now=now)
            if not isinstance(edit.category_key, Unchanged):
                if edit.category_key != expense.category_key:
                    self._require_active_category(edit.category_key)
                expense = expense.edit_category(category_key=edit.category_key, now=now)
            if not isinstance(edit.note, Unchanged):
                expense = expense.edit_note(note=edit.note, now=now)
        except ValueError as error:
            raise ExpenseEditRejected(str(error)) from error
        return expense

    def _require_acceptable_date(
        self, transaction_date: TransactionDate, *, caller_id: UUID, now: datetime
    ) -> None:
        user = self._user_repository.get(caller_id)
        if user is None:
            raise ExpenseNotFound("owner not found")
        if not is_within_future_tolerance(transaction_date, now=now, timezone=user.timezone):
            raise ExpenseEditRejected("transaction date is more than one day in the future")

    def _require_active_category(self, category_key: str) -> None:
        category = self._category_repository.get_by_key(category_key)
        if category is None or not category.is_active:
            raise ExpenseEditRejected("category does not exist")
