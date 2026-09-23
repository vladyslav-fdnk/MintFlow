"""Parsing and formatting of typed Telegram input (docs/telegram_client_design.md, T6).

Pure functions: no I/O, no clock. Every failure is a ValueError the flow turns
into a short instruction; nothing here guesses a value the user did not type.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from mintflow.domain.capture import CurrencyCode, Money, TransactionDate

_AMOUNT: Final = re.compile(r"(?P<number>[0-9]+(?:[.,][0-9]+)?)(?:\s*(?P<currency>[A-Za-z]{3}))?")
_ISO_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DOTTED_DATE: Final = re.compile(r"(?P<day>[0-9]{1,2})\.(?P<month>[0-9]{1,2})\.(?P<year>[0-9]{4})")


@dataclass(frozen=True, slots=True)
class TypedAmount:
    """An amount as typed, with the currency only when the user typed one."""

    value: Decimal
    currency: CurrencyCode | None


def parse_amount(text: str) -> TypedAmount:
    """``12.50``, ``12,50``, or ``12.50 EUR``. Zero, negatives, and signs are rejected."""
    match = _AMOUNT.fullmatch(text.strip())
    if match is None:
        raise ValueError("not an amount")
    try:
        value = Decimal(match.group("number").replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("not an amount") from error
    if value <= 0:
        raise ValueError("amount must be greater than zero")
    currency_text = match.group("currency")
    return TypedAmount(
        value=value, currency=CurrencyCode(currency_text) if currency_text is not None else None
    )


def to_money(value: Decimal, currency: CurrencyCode) -> Money:
    """Convert a typed amount into exact minor units; too many decimals is an error."""
    scaled = value.scaleb(currency.minor_unit_exponent)
    if scaled != scaled.to_integral_value():
        raise ValueError(
            f"{currency.value} allows at most {currency.minor_unit_exponent} decimal places"
        )
    return Money(minor_units=int(scaled), currency=currency)


def parse_currency(text: str) -> CurrencyCode:
    return CurrencyCode(text.strip())


def parse_date(text: str) -> TransactionDate:
    """``YYYY-MM-DD`` or ``DD.MM.YYYY``; the future-date rule is checked by the caller."""
    candidate = text.strip()
    try:
        if _ISO_DATE.fullmatch(candidate):
            return TransactionDate(date.fromisoformat(candidate))
        dotted = _DOTTED_DATE.fullmatch(candidate)
        if dotted:
            return TransactionDate(
                date(int(dotted["year"]), int(dotted["month"]), int(dotted["day"]))
            )
    except ValueError as error:
        raise ValueError("not a valid date") from error
    raise ValueError("not a date")


def format_money(money: Money) -> str:
    exponent = money.currency.minor_unit_exponent
    major = Decimal(money.minor_units).scaleb(-exponent)
    return f"{major:,.{exponent}f} {money.currency.value}"


def format_date(value: date) -> str:
    return value.strftime("%d %b %Y")
