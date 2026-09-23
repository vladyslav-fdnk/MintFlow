"""Immutable change records for confirmed Expenses (product decision 7, design D1).

A restrained audit trail, not event sourcing: one record per effective edit,
deletion, or restoration. It is a supporting persistence record, not a domain
aggregate, and is never shown to users this sprint.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from mintflow.domain.capture import Expense

type FieldValue = str | int | None


class ExpenseChangeType(StrEnum):
    EDITED = "edited"
    DELETED = "deleted"
    RESTORED = "restored"


class ExpenseField(StrEnum):
    """The user-editable Expense fields, as named in stored change records.

    Money is recorded as two fields so a currency-only correction does not
    restate the amount.
    """

    AMOUNT_MINOR_UNITS = "amount_minor_units"
    CURRENCY = "currency"
    TRANSACTION_DATE = "transaction_date"
    MERCHANT = "merchant"
    CATEGORY_KEY = "category_key"
    NOTE = "note"


_NULLABLE_FIELDS = frozenset({ExpenseField.MERCHANT, ExpenseField.NOTE})


def _check_value(expense_field: ExpenseField, value: FieldValue) -> None:
    if value is None:
        if expense_field not in _NULLABLE_FIELDS:
            raise ValueError(f"{expense_field} cannot be null")
        return
    if expense_field is ExpenseField.AMOUNT_MINOR_UNITS:
        if type(value) is not int:
            raise ValueError("amount_minor_units must be an integer")
    elif not isinstance(value, str):
        raise ValueError(f"{expense_field} must be a string")


@dataclass(frozen=True, slots=True)
class ExpenseFieldChange:
    field: ExpenseField
    old: FieldValue
    new: FieldValue

    def __post_init__(self) -> None:
        _check_value(self.field, self.old)
        _check_value(self.field, self.new)
        if self.old == self.new:
            raise ValueError("a field change must change the value")


def expense_field_values(expense: Expense) -> dict[ExpenseField, FieldValue]:
    """Serialize an Expense's editable fields to the JSON-safe form records store."""
    return {
        ExpenseField.AMOUNT_MINOR_UNITS: expense.money.minor_units,
        ExpenseField.CURRENCY: expense.money.currency.value,
        ExpenseField.TRANSACTION_DATE: expense.transaction_date.value.isoformat(),
        ExpenseField.MERCHANT: expense.merchant.value if expense.merchant is not None else None,
        ExpenseField.CATEGORY_KEY: expense.category_key,
        ExpenseField.NOTE: expense.note,
    }


@dataclass(frozen=True, slots=True)
class ExpenseChangeRecord:
    expense_id: UUID
    actor_user_id: UUID
    occurred_at: datetime
    change_type: ExpenseChangeType
    changes: tuple[ExpenseFieldChange, ...] = ()
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        if self.change_type is ExpenseChangeType.EDITED:
            if not self.changes:
                raise ValueError("an edited record must list its field changes")
            fields = [change.field for change in self.changes]
            if len(fields) != len(set(fields)):
                raise ValueError("each field may change at most once per record")
        elif self.changes:
            raise ValueError(f"a {self.change_type} record carries no field changes")

    def changes_document(self) -> dict[str, dict[str, FieldValue]] | None:
        """The stored JSON form: ``{field: {"old": ..., "new": ...}}``, or None."""
        if self.change_type is not ExpenseChangeType.EDITED:
            return None
        return {
            change.field.value: {"old": change.old, "new": change.new} for change in self.changes
        }


class ExpenseChangeRecordAppender(Protocol):
    def append(self, record: ExpenseChangeRecord) -> None: ...
