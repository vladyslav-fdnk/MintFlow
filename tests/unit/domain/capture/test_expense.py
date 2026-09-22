from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from mintflow.domain.capture import (
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)

OWNER = uuid4()
DRAFT_ID = uuid4()
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
MONEY = Money(minor_units=1000, currency=CurrencyCode("USD"))
TX_DATE = TransactionDate(date(2026, 8, 3))


def _expense(**overrides: object) -> Expense:
    kwargs: dict[str, object] = {
        "owner_id": OWNER,
        "money": MONEY,
        "transaction_date": TX_DATE,
        "category_key": "groceries",
        "capture_draft_id": DRAFT_ID,
        "source": CaptureSource.WEB_MANUAL,
        "now": NOW,
    }
    kwargs.update(overrides)
    return Expense.create(**kwargs)  # type: ignore[arg-type]


def test_create_succeeds_with_positive_money_and_a_category() -> None:
    expense = _expense()

    assert expense.money == MONEY
    assert expense.category_key == "groceries"
    assert expense.is_active is True
    assert expense.deleted_at is None
    assert expense.created_at == NOW
    assert expense.modified_at == NOW


def test_create_rejects_zero_amount() -> None:
    zero = Money(minor_units=0, currency=CurrencyCode("USD"))

    with pytest.raises(ValueError, match="strictly positive"):
        _expense(money=zero)


def test_create_rejects_negative_amount() -> None:
    with pytest.raises(ValueError):
        Money(minor_units=-1, currency=CurrencyCode("USD"))


def test_create_rejects_missing_category() -> None:
    with pytest.raises(ValueError, match="stable lowercase identifier"):
        _expense(category_key="")


def test_edit_money_replaces_amount_and_currency_atomically() -> None:
    expense = _expense()
    new_money = Money(minor_units=2500, currency=CurrencyCode("EUR"))

    edited = expense.edit_money(money=new_money, now=NOW + timedelta(minutes=1))

    assert edited.money == new_money
    assert edited.modified_at == NOW + timedelta(minutes=1)
    assert expense.money == MONEY


def test_edit_money_rejects_a_non_positive_replacement() -> None:
    expense = _expense()
    zero = Money(minor_units=0, currency=CurrencyCode("USD"))

    with pytest.raises(ValueError, match="strictly positive"):
        expense.edit_money(money=zero, now=NOW)


def test_edit_merchant_updates_value_and_modified_at() -> None:
    expense = _expense()
    merchant = MerchantName("Coffee Shop")

    edited = expense.edit_merchant(merchant=merchant, now=NOW + timedelta(minutes=1))

    assert edited.merchant == merchant
    assert edited.modified_at == NOW + timedelta(minutes=1)
    assert expense.merchant is None


def test_edit_category_updates_value_and_modified_at() -> None:
    expense = _expense()

    edited = expense.edit_category(category_key="travel", now=NOW + timedelta(minutes=1))

    assert edited.category_key == "travel"
    assert edited.modified_at == NOW + timedelta(minutes=1)
    assert expense.category_key == "groceries"


def test_edit_note_updates_value_and_modified_at() -> None:
    expense = _expense()

    edited = expense.edit_note(note="Team lunch", now=NOW + timedelta(minutes=1))

    assert edited.note == "Team lunch"
    assert edited.modified_at == NOW + timedelta(minutes=1)
    assert expense.note is None


def test_edit_transaction_date_updates_value_and_modified_at() -> None:
    expense = _expense()
    new_date = TransactionDate(date(2026, 8, 1))

    edited = expense.edit_transaction_date(
        transaction_date=new_date, now=NOW + timedelta(minutes=1)
    )

    assert edited.transaction_date == new_date
    assert edited.modified_at == NOW + timedelta(minutes=1)
    assert expense.transaction_date == TX_DATE


def test_delete_sets_deleted_at_exactly_once() -> None:
    expense = _expense()

    deleted = expense.delete(now=NOW + timedelta(minutes=1))
    twice_deleted = deleted.delete(now=NOW + timedelta(minutes=2))

    assert deleted.deleted_at == NOW + timedelta(minutes=1)
    assert twice_deleted.deleted_at == NOW + timedelta(minutes=1)
    assert twice_deleted is deleted


def test_restore_clears_deleted_at() -> None:
    deleted = _expense().delete(now=NOW + timedelta(minutes=1))

    restored = deleted.restore(now=NOW + timedelta(minutes=2))

    assert restored.deleted_at is None
    assert restored.modified_at == NOW + timedelta(minutes=2)


def test_restore_on_an_active_expense_is_a_no_op() -> None:
    expense = _expense()

    restored = expense.restore(now=NOW + timedelta(minutes=1))

    assert restored is expense


def test_is_active_reflects_deleted_at() -> None:
    expense = _expense()
    assert expense.is_active is True

    deleted = expense.delete(now=NOW)
    assert deleted.is_active is False


def test_original_instance_is_unchanged_after_every_transition() -> None:
    expense = _expense()

    expense.edit_money(money=Money(minor_units=1, currency=CurrencyCode("USD")), now=NOW)
    expense.edit_merchant(merchant=MerchantName("Somewhere"), now=NOW)
    expense.edit_category(category_key="other", now=NOW)
    expense.edit_note(note="note", now=NOW)
    expense.edit_transaction_date(transaction_date=TransactionDate(date(2026, 1, 1)), now=NOW)
    expense.delete(now=NOW)

    assert expense.money == MONEY
    assert expense.merchant is None
    assert expense.category_key == "groceries"
    assert expense.note is None
    assert expense.transaction_date == TX_DATE
    assert expense.deleted_at is None
