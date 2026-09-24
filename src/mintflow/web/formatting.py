"""Locale-aware display of money and dates, and translated strings (web design W7, W14).

Amounts are shown with exactly their currency's minor-unit digits and always with the ISO code,
never a symbol that could be ambiguous. Separators follow the user's locale; an unset or unknown
locale falls back to English. A converted amount is always marked "≈", and a page that shows one
says which rates it used (docs/exchange_rates_design.md, section 2).
"""

from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from gettext import c2py
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from babel import Locale as BabelLocale
from babel import UnknownLocaleError
from babel.dates import format_date as babel_format_date
from babel.dates import format_datetime as babel_format_datetime
from babel.dates import format_interval, format_skeleton
from babel.messages.pofile import read_po
from babel.numbers import format_decimal, format_percent

from mintflow.application.rates import ExchangeRate
from mintflow.domain.capture import CurrencyCode, Money
from mintflow.domain.user import Locale, Timezone

DEFAULT_LOCALE: Final = "en"
# Keeps an amount and its currency code on one line.
_NO_BREAK_SPACE: Final = "\u00a0"
APPROXIMATELY: Final = "\u2248"


DEFAULT_LANGUAGE: Final = "en"
SUPPORTED_LANGUAGES: Final = ("en", "ru")
LOCALE_DIRECTORY: Final = Path(__file__).parent / "locale"
# The interface language of the current request (design W14).
_language: ContextVar[str] = ContextVar("language", default=DEFAULT_LANGUAGE)


@dataclass(frozen=True, slots=True)
class _Catalogue:
    messages: dict[str, str | tuple[str, ...]]
    plural: Callable[[int], int]


@cache
def _catalogue(language: str) -> _Catalogue | None:
    """The language's translations, read from its .po file once per process."""
    path = LOCALE_DIRECTORY / f"{language}.po"
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        catalogue = read_po(handle, locale=language)
    messages: dict[str, str | tuple[str, ...]] = {}
    for message in catalogue:
        if not message.id or message.fuzzy:
            continue
        key = str(message.id if isinstance(message.id, str) else message.id[0])
        if isinstance(message.string, str):
            if message.string:
                messages[key] = message.string
        elif message.string is not None and all(message.string):
            messages[key] = tuple(str(form) for form in message.string)
    return _Catalogue(messages=messages, plural=c2py(catalogue.plural_expr))


def use_language(language: str | None) -> None:
    """Set the current request's language; unknown values fall back to English."""
    _language.set(language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE)


def current_language() -> str:
    return _language.get()


def negotiate_language(accept_language: str) -> str:
    """The first supported language in an Accept-Language header, else English."""
    ranked = []
    for index, part in enumerate(accept_language.split(",")):
        tag, _separator, parameters = part.strip().partition(";")
        quality = 1.0
        if parameters.strip().startswith("q="):
            try:
                quality = float(parameters.strip()[2:])
            except ValueError:
                quality = 0.0
        if quality > 0:  # q=0 means "not acceptable" (RFC 9110)
            ranked.append((-quality, index, tag.strip().split("-")[0].lower()))
    for _quality, _index, language in sorted(ranked):
        if language in SUPPORTED_LANGUAGES:
            return language
    return DEFAULT_LANGUAGE


def _(text: str) -> str:
    """Translate a user-facing string into the current request's language."""
    catalogue = _catalogue(current_language())
    translated = catalogue.messages.get(text) if catalogue is not None else None
    return translated if isinstance(translated, str) else text


def sentence(text: str) -> str:
    """Drop the doubled full stop when a sentence ends with a value that has one ("2026 г.")."""
    return text[:-1] if text.endswith("..") and not text.endswith("...") else text


def N_(text: str) -> str:
    """Mark a string for translation where it is defined; ``_()`` translates it when shown."""
    return text


def display_locale(locale: Locale | None) -> BabelLocale:
    """The user's number and date format, else the interface language's."""
    if locale is not None:
        try:
            return BabelLocale.parse(locale.value, sep="-")
        except (ValueError, UnknownLocaleError):
            pass
    return BabelLocale.parse(current_language())


def format_money(money: Money, locale: BabelLocale) -> str:
    return format_amount(money.minor_units, money.currency, locale)


def format_amount(minor_units: int, currency: CurrencyCode, locale: BabelLocale) -> str:
    """Any amount in minor units, including sums above what one Money may hold."""
    exponent = currency.minor_unit_exponent
    major = Decimal(minor_units).scaleb(-exponent)
    pattern = "#,##0" + ("." + "0" * exponent if exponent else "")
    number = format_decimal(major, format=pattern, locale=locale)
    return f"{number}{_NO_BREAK_SPACE}{currency.value}"


def format_axis_amount(minor_units: int, currency: CurrencyCode, locale: BabelLocale) -> str:
    """A compact amount for chart axes: no trailing zero decimals ("150 EUR", "12.5 EUR")."""
    major = Decimal(minor_units).scaleb(-currency.minor_unit_exponent)
    pattern = "#,##0" + (
        "." + "#" * currency.minor_unit_exponent if currency.minor_unit_exponent else ""
    )
    return (
        f"{format_decimal(major, format=pattern, locale=locale)}{_NO_BREAK_SPACE}{currency.value}"
    )


def approximately(text: str) -> str:
    """Mark a converted amount (or any text made of one) as approximate."""
    return f"{APPROXIMATELY}{_NO_BREAK_SPACE}{text}"


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
    """The form for ``count`` in the current language (Russian has three)."""
    catalogue = _catalogue(current_language())
    forms = catalogue.messages.get(singular) if catalogue is not None else None
    if isinstance(forms, tuple):
        assert catalogue is not None
        return forms[min(catalogue.plural(count), len(forms) - 1)]
    return singular if count == 1 else plural


# Display labels of the system categories (MVP section 6: identities stay, labels may be
# localized). A category missing here shows its stored name.
_CATEGORY_LABELS: Final = {
    "groceries": N_("Groceries"),
    "food_and_dining": N_("Food & Dining"),
    "transport": N_("Transport"),
    "shopping": N_("Shopping"),
    "housing": N_("Housing"),
    "utilities": N_("Utilities"),
    "health": N_("Health"),
    "entertainment": N_("Entertainment"),
    "travel": N_("Travel"),
    "education": N_("Education"),
    "gifts": N_("Gifts"),
    "other": N_("Other"),
    "uncategorized": N_("Uncategorized"),
}


def category_label(key: str, stored_name: str) -> str:
    """A category's name in the current language."""
    label = _CATEGORY_LABELS.get(key)
    return _(label) if label is not None else stored_name


_RATE_SOURCES: Final = {
    "ECB": N_("European Central Bank"),
    "NBU": N_("National Bank of Ukraine"),
}


def rates_note(rates: Iterable[ExchangeRate], currency: CurrencyCode, locale: BabelLocale) -> str:
    """Which rates converted the amounts marked "≈": the oldest rate date and every source."""
    rates = tuple(rates)
    if not rates:
        raise ValueError("a note needs at least one rate")
    sources = sorted({rate.source for rate in rates})
    return sentence(
        _("≈ Converted into {currency} at the exchange rates of {date} ({sources}).").format(
            currency=currency.value,
            date=format_date(min(rate.rate_date for rate in rates), locale),
            sources=", ".join(
                _(_RATE_SOURCES[source]) if source in _RATE_SOURCES else source
                for source in sources
            ),
        )
    )
