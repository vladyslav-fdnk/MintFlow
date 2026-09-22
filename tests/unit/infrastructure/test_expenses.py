from datetime import UTC, date, datetime
from uuid import uuid4

from mintflow.domain.capture import (
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.infrastructure.persistence.expenses import _to_domain, _to_record

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
OWNER = uuid4()
DRAFT_ID = uuid4()


def _expense(**overrides: object) -> Expense:
    kwargs: dict[str, object] = {
        "owner_id": OWNER,
        "money": Money(minor_units=1500, currency=CurrencyCode("USD")),
        "transaction_date": TransactionDate(date(2026, 8, 4)),
        "category_key": "groceries",
        "capture_draft_id": DRAFT_ID,
        "source": CaptureSource.WEB_MANUAL,
        "now": NOW,
    }
    kwargs.update(overrides)
    return Expense.create(**kwargs)  # type: ignore[arg-type]


def test_round_trips_a_fully_populated_expense() -> None:
    expense = _expense(
        merchant=MerchantName("Coffee Shop"),
        note="Team lunch",
        receipt_id=uuid4(),
    )

    restored = _to_domain(_to_record(expense))

    assert restored == expense


def test_round_trips_a_minimally_populated_expense() -> None:
    expense = _expense()

    restored = _to_domain(_to_record(expense))

    assert restored == expense
    assert restored.merchant is None
    assert restored.note is None
    assert restored.receipt_id is None


def test_round_trips_a_deleted_expense() -> None:
    deleted = _expense().delete(now=NOW)

    restored = _to_domain(_to_record(deleted))

    assert restored == deleted
    assert restored.deleted_at == NOW
    assert restored.is_active is False
