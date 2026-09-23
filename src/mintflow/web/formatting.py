"""Locale-aware display of money and dates, and externalizable strings (web design W7).

Amounts are shown with exactly their currency's minor-unit digits and always with the ISO code,
never a symbol that could be ambiguous. Separators follow the user's locale; an unset or unknown
locale falls back to English.
"""

from datetime import date
from decimal import Decimal
from typing import Final

from babel import Locale as BabelLocale
from babel import UnknownLocaleError
from babel.dates import format_date as babel_format_date
from babel.numbers import format_decimal

from mintflow.domain.capture import Money
from mintflow.domain.user import Locale

DEFAULT_LOCALE: Final = "en"
# Keeps an amount and its currency code on one line.
_NO_BREAK_SPACE: Final = " "


def _(text: str) -> str:
    """Mark a user-facing string for translation; English is the only language for now."""
    return text


def display_locale(locale: Locale | None) -> BabelLocale:
    if locale is not None:
        try:
            return BabelLocale.parse(locale.value, sep="-")
        except (ValueError, UnknownLocaleError):
            pass
    return BabelLocale.parse(DEFAULT_LOCALE)


def format_money(money: Money, locale: BabelLocale) -> str:
    exponent = money.currency.minor_unit_exponent
    major = Decimal(money.minor_units).scaleb(-exponent)
    pattern = "#,##0" + ("." + "0" * exponent if exponent else "")
    number = format_decimal(major, format=pattern, locale=locale)
    return f"{number}{_NO_BREAK_SPACE}{money.currency.value}"


def format_date(value: date, locale: BabelLocale) -> str:
    return babel_format_date(value, format="medium", locale=locale)
