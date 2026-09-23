"""The expense edit form: display values, validation, and the resulting edit (web design W2).

The form carries the values it was rendered with. Only fields the user changed become part of
the ``ExpenseEdit``, so submitting a form that was opened before someone else changed another
field never overwrites that change. Amounts are read in the user's locale ("12,50" in German).
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass, fields, replace
from datetime import date
from decimal import Decimal
from typing import Final

from babel import Locale as BabelLocale
from babel.numbers import NumberFormatError, format_decimal, parse_decimal

from mintflow.application.capture import UNCHANGED, ExpenseEdit
from mintflow.domain.capture import CurrencyCode, Expense, MerchantName, Money, TransactionDate
from mintflow.http.capture import MAX_NOTE_LENGTH
from mintflow.web.formatting import _

ORIGINAL_PREFIX: Final = "was_"


@dataclass(frozen=True, slots=True)
class ExpenseForm:
    """Every field as text, exactly as the form shows or submits it."""

    amount: str
    currency: str
    transaction_date: str
    merchant: str
    category_key: str
    note: str

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(field.name for field in fields(cls))

    @classmethod
    def from_mapping(cls, values: dict[str, str], *, prefix: str = "") -> "ExpenseForm | None":
        try:
            return cls(**{name: values[prefix + name] for name in cls.field_names()})
        except KeyError:
            return None


def _amount_text(money: Money, locale: BabelLocale) -> str:
    exponent = money.currency.minor_unit_exponent
    major = Decimal(money.minor_units).scaleb(-exponent)
    pattern = "0" + ("." + "0" * exponent if exponent else "")
    return format_decimal(major, format=pattern, locale=locale)


def form_from_expense(expense: Expense, locale: BabelLocale) -> ExpenseForm:
    return ExpenseForm(
        amount=_amount_text(expense.money, locale),
        currency=expense.money.currency.value,
        transaction_date=expense.transaction_date.value.isoformat(),
        merchant=expense.merchant.value if expense.merchant is not None else "",
        category_key=expense.category_key,
        note=expense.note or "",
    )


@dataclass(frozen=True, slots=True)
class ValidatedEdit:
    edit: ExpenseEdit
    errors: dict[str, str]

    @property
    def changes_anything(self) -> bool:
        return any(getattr(self.edit, field.name) is not UNCHANGED for field in fields(self.edit))


def _money(amount: str, currency: str, locale: BabelLocale) -> Money | tuple[str, str]:
    """The Money, or the field to blame and its message."""
    try:
        code = CurrencyCode(currency)
    except ValueError:
        return "currency", _("Choose a currency.")
    try:
        value = parse_decimal(amount.strip(), locale=locale, strict=True)
    except NumberFormatError:
        example = format_decimal(Decimal("12.50"), format="0.00", locale=locale)
        return "amount", _("Enter an amount like {example}.").format(example=example)
    minor = value.scaleb(code.minor_unit_exponent)
    if minor != minor.to_integral_value():
        return "amount", _("{currency} amounts have at most {digits} decimal places.").format(
            currency=code.value, digits=code.minor_unit_exponent
        )
    try:
        if minor <= 0:
            raise ValueError("not positive")
        return Money(minor_units=int(minor), currency=code)
    except ValueError:
        return "amount", _("Enter an amount greater than zero and within the supported range.")


def validate_edit(
    submitted: ExpenseForm,
    original: ExpenseForm,
    *,
    locale: BabelLocale,
    category_keys: Collection[str],
    date_is_acceptable: Callable[[TransactionDate], bool],
) -> ValidatedEdit:
    """Build the edit from changed fields only, with one message per invalid field."""
    edit = ExpenseEdit()
    errors: dict[str, str] = {}

    if (submitted.amount, submitted.currency) != (original.amount, original.currency):
        money = _money(submitted.amount, submitted.currency, locale)
        if isinstance(money, Money):
            edit = replace(edit, money=money)
        else:
            field, message = money
            errors[field] = message

    if submitted.transaction_date != original.transaction_date:
        try:
            transaction_date = TransactionDate(date.fromisoformat(submitted.transaction_date))
        except ValueError:
            errors["transaction_date"] = _("Enter a date.")
        else:
            if date_is_acceptable(transaction_date):
                edit = replace(edit, transaction_date=transaction_date)
            else:
                errors["transaction_date"] = _("The date can be at most one day in the future.")

    if submitted.merchant.strip() != original.merchant.strip():
        if not submitted.merchant.strip():
            edit = replace(edit, merchant=None)
        else:
            try:
                edit = replace(edit, merchant=MerchantName(submitted.merchant))
            except ValueError:
                errors["merchant"] = _("Use at most {limit} characters.").format(
                    limit=MerchantName.MAX_LENGTH
                )

    if submitted.category_key != original.category_key:
        if submitted.category_key in category_keys:
            edit = replace(edit, category_key=submitted.category_key)
        else:
            errors["category_key"] = _("Choose a category.")

    if submitted.note.strip() != original.note.strip():
        note = submitted.note.strip()
        if len(note) > MAX_NOTE_LENGTH:
            errors["note"] = _("Use at most {limit} characters.").format(limit=MAX_NOTE_LENGTH)
        else:
            edit = replace(edit, note=note or None)

    return ValidatedEdit(edit=edit, errors=errors)
