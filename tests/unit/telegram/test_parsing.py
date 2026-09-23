from datetime import date
from decimal import Decimal

import pytest

from mintflow.domain.capture import CurrencyCode, Money, TransactionDate
from mintflow.telegram.parsing import (
    format_date,
    format_money,
    parse_amount,
    parse_currency,
    parse_date,
    to_money,
)

EUR, JPY, BHD = CurrencyCode("EUR"), CurrencyCode("JPY"), CurrencyCode("BHD")


@pytest.mark.parametrize(
    ("text", "value", "currency"),
    [
        ("12.50", Decimal("12.50"), None),
        ("12,50", Decimal("12.50"), None),
        ("  7 ", Decimal("7"), None),
        ("12.50 EUR", Decimal("12.50"), EUR),
        ("12.50eur", Decimal("12.50"), EUR),
        ("1500 jpy", Decimal("1500"), JPY),
    ],
)
def test_parse_amount_accepts_plain_numbers_with_optional_currency(
    text: str, value: Decimal, currency: CurrencyCode | None
) -> None:
    typed = parse_amount(text)

    assert (typed.value, typed.currency) == (value, currency)


@pytest.mark.parametrize(
    "text",
    ["", "0", "0.00", "-5", "+5", "1e5", "12.", ".5", "12.50.3", "twelve", "12 50", "12.50 EURO"],
)
def test_parse_amount_rejects_everything_else(text: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(text)


def test_parse_amount_rejects_unknown_currencies() -> None:
    with pytest.raises(ValueError):
        parse_amount("12.50 ZZZ")


@pytest.mark.parametrize(
    ("value", "currency", "minor_units"),
    [
        (Decimal("12.5"), EUR, 1250),
        (Decimal("12.50"), EUR, 1250),
        (Decimal("1500"), JPY, 1500),
        (Decimal("1.234"), BHD, 1234),
    ],
)
def test_to_money_uses_the_currency_precision(
    value: Decimal, currency: CurrencyCode, minor_units: int
) -> None:
    assert to_money(value, currency) == Money(minor_units=minor_units, currency=currency)


@pytest.mark.parametrize(
    ("value", "currency"),
    [(Decimal("12.505"), EUR), (Decimal("1500.5"), JPY), (Decimal("1.2345"), BHD)],
)
def test_to_money_rejects_more_decimals_than_the_currency_allows(
    value: Decimal, currency: CurrencyCode
) -> None:
    with pytest.raises(ValueError, match="decimal places"):
        to_money(value, currency)


def test_to_money_rejects_amounts_over_the_domain_maximum() -> None:
    with pytest.raises(ValueError):
        to_money(Decimal("1000000000"), EUR)


def test_parse_currency_normalizes_case() -> None:
    assert parse_currency(" usd ") == CurrencyCode("USD")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-23", date(2026, 9, 23)),
        ("23.09.2026", date(2026, 9, 23)),
        ("1.2.2026", date(2026, 2, 1)),
    ],
)
def test_parse_date(text: str, expected: date) -> None:
    assert parse_date(text) == TransactionDate(expected)


@pytest.mark.parametrize(
    "text", ["", "yesterday", "2026-02-30", "31.02.2026", "23/09/2026", "23.09.26", "1999-12-31"]
)
def test_parse_date_rejects_other_input(text: str) -> None:
    with pytest.raises(ValueError):
        parse_date(text)


@pytest.mark.parametrize(
    ("money", "text"),
    [
        (Money(minor_units=123450, currency=EUR), "1,234.50 EUR"),
        (Money(minor_units=1500, currency=JPY), "1,500 JPY"),
        (Money(minor_units=1234, currency=BHD), "1.234 BHD"),
        (Money(minor_units=5, currency=EUR), "0.05 EUR"),
    ],
)
def test_format_money_respects_minor_units(money: Money, text: str) -> None:
    assert format_money(money) == text


def test_format_date() -> None:
    assert format_date(date(2026, 9, 3)) == "03 Sep 2026"
