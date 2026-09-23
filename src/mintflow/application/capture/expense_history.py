"""Expense history listing: filters, keyset positions, and the opaque cursor codec.

The history order is ``transaction_date DESC, created_at DESC, id DESC``
(docs/expense_management_design.md, D5). A position is the sort key of the
last Expense on a page; the next page starts strictly after it.

The cursor is unsigned base64url JSON. It only narrows a query that is
already owner-scoped, so a tampered cursor cannot reveal another user's data.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from mintflow.domain.capture import CurrencyCode, Expense

MAX_HISTORY_PAGE_SIZE = 100
_MAX_CURSOR_LENGTH = 256
_CURSOR_KEYS = frozenset({"d", "c", "i"})


class InvalidExpenseHistoryCursor(ValueError):
    """The presented cursor is not one this service could have issued."""


@dataclass(frozen=True, slots=True)
class ExpenseHistoryPosition:
    transaction_date: date
    created_at: datetime
    expense_id: UUID

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    @classmethod
    def after(cls, expense: Expense) -> "ExpenseHistoryPosition":
        return cls(
            transaction_date=expense.transaction_date.value,
            created_at=expense.created_at,
            expense_id=expense.id,
        )


@dataclass(frozen=True, slots=True)
class ExpenseHistoryFilter:
    """Filters combined with AND. Date bounds are inclusive; empty sets mean "any"."""

    date_from: date | None = None
    date_to: date | None = None
    category_keys: frozenset[str] = frozenset()
    currencies: frozenset[CurrencyCode] = frozenset()

    def __post_init__(self) -> None:
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("date_from must not be after date_to")


@dataclass(frozen=True, slots=True)
class ExpenseHistoryPage:
    items: tuple[Expense, ...]
    next_position: ExpenseHistoryPosition | None


def encode_history_cursor(position: ExpenseHistoryPosition) -> str:
    payload = json.dumps(
        {
            "d": position.transaction_date.isoformat(),
            "c": position.created_at.isoformat(),
            "i": str(position.expense_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")


def decode_history_cursor(cursor: str) -> ExpenseHistoryPosition:
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise InvalidExpenseHistoryCursor
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.keys() != _CURSOR_KEYS:
            raise InvalidExpenseHistoryCursor
        values = [payload["d"], payload["c"], payload["i"]]
        if not all(isinstance(value, str) for value in values):
            raise InvalidExpenseHistoryCursor
        position = ExpenseHistoryPosition(
            transaction_date=date.fromisoformat(payload["d"]),
            created_at=datetime.fromisoformat(payload["c"]),
            expense_id=UUID(payload["i"]),
        )
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise InvalidExpenseHistoryCursor from error
    # Accept only the canonical form this service issues.
    if encode_history_cursor(position) != cursor:
        raise InvalidExpenseHistoryCursor
    return position
