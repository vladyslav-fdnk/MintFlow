"""Locale-aware display of money and dates, and externalizable strings (web design W7).

Amounts are shown with exactly their currency's minor-unit digits and always with the ISO code,
never a symbol that could be ambiguous. Separators follow the user's locale; an unset or unknown
locale falls back to English.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from babel import Locale as BabelLocale
from babel import UnknownLocaleError
from babel.dates import format_date as babel_format_date
from babel.dates import format_datetime as babel_format_datetime
from babel.dates import format_interval, format_skeleton
from babel.numbers import format_decimal, format_percent

from mintflow.domain.capture import CurrencyCode, Money
from mintflow.domain.user import Locale, Timezone

DEFAULT_LOCALE: Final = "en"
# Keeps an amount and its currency code on one line.
_NO_BREAK_SPACE: Final = "\u00a0"


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
    return format_amount(money.minor_units, money.currency, locale)


def format_amount(minor_units: int, currency: CurrencyCode, locale: BabelLocale) -> str:
    """Any amount in minor units, including sums above what one Money may hold."""
    exponent = currency.minor_unit_exponent
    major = Decimal(minor_units).scaleb(-exponent)
    pattern = "#,##0" + ("." + "0" * exponent if exponent else "")
    number = format_decimal(major, format=pattern, locale=locale)
    return f"{number}{_NO_BREAK_SPACE}{currency.value}"


def format_date(value: date, locale: BabelLocale) -> str:
    return babel_format_date(value, format="medium", locale=locale)


def format_datetime(value: datetime, timezone: Timezone, locale: BabelLocale) -> str:
    """A moment, shown in the user's timezone."""
    return babel_format_datetime(
        value, format="medium", tzinfo=ZoneInfo(timezone.value), locale=locale
    )


def format_short_date(value: date, locale: BabelLocale) -> str:
    """Day and month only, for chart labels."""
    return format_skeleton("MMMd", value, locale=locale)


def format_month(value: date, locale: BabelLocale) -> str:
    return format_skeleton("yMMM", value, locale=locale)


def format_period(date_from: date, date_to: date, locale: BabelLocale) -> str:
    if date_from == date_to:
        return format_date(date_from, locale)
    return format_interval(date_from, date_to, skeleton="yMMMd", locale=locale)


def format_share(basis_points: int, locale: BabelLocale) -> str:
    """A share in basis points (1/100 of a percent) as a percentage with one decimal."""
    return format_percent(Decimal(basis_points) / 10_000, format="#,##0.#%", locale=locale)


def format_count(count: int, locale: BabelLocale) -> str:
    return format_decimal(count, locale=locale)


def ngettext(singular: str, plural: str, count: int) -> str:
    """Pick the singular or plural form; English rules until translations exist."""
    return singular if count == 1 else plural
