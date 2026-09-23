from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from babel import Locale as BabelLocale

from mintflow.application.capture import UNCHANGED
from mintflow.domain.capture import (
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.domain.user import Locale
from mintflow.web.expense_form import (
    ExpenseForm,
    ValidatedEdit,
    form_from_expense,
    validate_edit,
)
from mintflow.web.formatting import display_locale

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
EN = display_locale(None)
DE = display_locale(Locale("de-DE"))
CATEGORIES = {"groceries", "transport"}
EXPENSE = Expense.create(
    owner_id=uuid4(),
    money=Money(minor_units=1_250, currency=CurrencyCode("EUR")),
    transaction_date=TransactionDate(date(2026, 9, 20)),
    category_key="groceries",
    capture_draft_id=uuid4(),
    merchant=MerchantName("Corner Shop"),
    source=CaptureSource.TELEGRAM_MANUAL,
    now=NOW,
)
ORIGINAL = form_from_expense(EXPENSE, EN)


def _validate(
    submitted: ExpenseForm, *, locale: BabelLocale = EN, date_ok: bool = True
) -> ValidatedEdit:
    original = form_from_expense(EXPENSE, locale)
    return validate_edit(
        submitted,
        original,
        locale=locale,
        category_keys=CATEGORIES,
        date_is_acceptable=lambda _value: date_ok,
    )


def test_the_form_shows_the_expense_in_the_users_locale() -> None:
    assert ORIGINAL == ExpenseForm(
        amount="12.50",
        currency="EUR",
        transaction_date="2026-09-20",
        merchant="Corner Shop",
        category_key="groceries",
        note="",
    )
    assert form_from_expense(EXPENSE, DE).amount == "12,50"


def test_an_unchanged_form_changes_nothing() -> None:
    result = _validate(ORIGINAL)

    assert result.errors == {} and not result.changes_anything


def test_only_changed_fields_become_part_of_the_edit() -> None:
    result = _validate(replace(ORIGINAL, category_key="transport"))

    assert result.edit.category_key == "transport"
    assert result.edit.money is UNCHANGED
    assert result.edit.merchant is UNCHANGED
    assert result.edit.transaction_date is UNCHANGED


def test_amounts_are_read_in_the_users_locale() -> None:
    german = form_from_expense(EXPENSE, DE)

    result = _validate(replace(german, amount="1.234,56"), locale=DE)

    assert result.edit.money == Money(minor_units=123_456, currency=CurrencyCode("EUR"))


def test_a_new_currency_reads_the_amount_in_that_currencys_precision() -> None:
    assert _validate(replace(ORIGINAL, amount="1500", currency="JPY")).edit.money == Money(
        minor_units=1_500, currency=CurrencyCode("JPY")
    )
    assert _validate(replace(ORIGINAL, currency="JPY")).errors == {
        "amount": "JPY amounts have at most 0 decimal places."
    }


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"amount": "twelve"}, "amount", "Enter an amount like 12.50."),
        ({"amount": "0"}, "amount", "Enter an amount greater than zero"),
        ({"amount": "-3"}, "amount", "Enter an amount"),
        ({"amount": "12.505"}, "amount", "EUR amounts have at most 2 decimal places."),
        ({"amount": "99999999999"}, "amount", "within the supported range"),
        ({"currency": "XYZ"}, "currency", "Choose a currency."),
        ({"transaction_date": "20.09.2026"}, "transaction_date", "Enter a date."),
        ({"transaction_date": ""}, "transaction_date", "Enter a date."),
        ({"merchant": "x" * 141}, "merchant", "Use at most 140 characters."),
        ({"category_key": "retired"}, "category_key", "Choose a category."),
        ({"note": "n" * 2001}, "note", "Use at most 2000 characters."),
    ],
)
def test_each_invalid_field_gets_its_own_message(
    changes: dict[str, str], field: str, message: str
) -> None:
    result = _validate(replace(ORIGINAL, **changes))

    assert list(result.errors) == [field]
    assert message in result.errors[field]


def test_a_date_beyond_the_tolerance_is_refused() -> None:
    result = _validate(replace(ORIGINAL, transaction_date="2026-12-01"), date_ok=False)

    assert result.errors == {"transaction_date": "The date can be at most one day in the future."}


def test_merchant_and_note_can_be_cleared() -> None:
    with_note = replace(ORIGINAL, note="lunch")
    result = validate_edit(
        replace(ORIGINAL, merchant="  ", note=""),
        with_note,
        locale=EN,
        category_keys=CATEGORIES,
        date_is_acceptable=lambda _value: True,
    )

    assert result.errors == {}
    assert (result.edit.merchant, result.edit.note) == (None, None)


def test_a_stale_form_keeps_changes_made_elsewhere() -> None:
    # The form was opened when the merchant was "Corner Shop"; someone else renamed it since.
    # This submission only changes the amount, so the other change must survive.
    result = _validate(replace(ORIGINAL, amount="20.00"))

    assert result.edit.merchant is UNCHANGED
    assert result.edit.money == Money(minor_units=2_000, currency=CurrencyCode("EUR"))
