from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from mintflow.application.rates import ExchangeRate
from mintflow.domain.capture import CurrencyCode, Money
from mintflow.domain.user import Locale
from mintflow.web.formatting import (
    approximately,
    display_locale,
    format_date,
    format_money,
    rates_note,
    use_language,
)

NBSP = "\u00a0"
NARROW_NBSP = "\u202f"


def _money(minor_units: int, currency: str) -> Money:
    return Money(minor_units=minor_units, currency=CurrencyCode(currency))


@pytest.mark.parametrize(
    ("locale", "money", "expected"),
    [
        pytest.param("en", _money(123_450, "EUR"), f"1,234.50{NBSP}EUR", id="english"),
        pytest.param("de-DE", _money(123_450, "EUR"), f"1.234,50{NBSP}EUR", id="german"),
        pytest.param(
            "fr-FR", _money(123_450, "EUR"), f"1{NARROW_NBSP}234,50{NBSP}EUR", id="french"
        ),
        pytest.param("en", _money(1_500, "JPY"), f"1,500{NBSP}JPY", id="no minor units"),
        pytest.param("en", _money(1_250, "BHD"), f"1.250{NBSP}BHD", id="three decimals"),
        pytest.param("en", _money(5, "USD"), f"0.05{NBSP}USD", id="cents only"),
    ],
)
def test_amounts_use_the_locale_the_currency_precision_and_the_iso_code(
    locale: str, money: Money, expected: str
) -> None:
    assert format_money(money, display_locale(Locale(locale))) == expected


@pytest.mark.parametrize("locale", [None, Locale("xx-QQ")])
def test_an_unset_or_unknown_locale_falls_back_to_english(locale: Locale | None) -> None:
    assert display_locale(locale).language == "en"


@pytest.mark.parametrize(
    ("locale", "expected"),
    [("en", "Sep 20, 2026"), ("de-DE", "20.09.2026"), ("en-GB", "20 Sept 2026")],
)
def test_dates_follow_the_locale(locale: str, expected: str) -> None:
    assert format_date(date(2026, 9, 20), display_locale(Locale(locale))) == expected


RATES = (
    ExchangeRate(CurrencyCode("USD"), Decimal("1.25"), date(2026, 9, 23), "ECB"),
    ExchangeRate(CurrencyCode("UAH"), Decimal("48"), date(2026, 9, 24), "NBU"),
)


@pytest.fixture
def _english_afterwards() -> Iterator[None]:
    yield
    use_language("en")


def test_a_converted_amount_is_marked_approximately() -> None:
    assert approximately("24.00\u00a0EUR") == "\u2248\u00a024.00\u00a0EUR"


def test_the_rates_note_gives_the_oldest_date_and_every_source() -> None:
    assert rates_note(RATES, CurrencyCode("EUR"), display_locale(None)) == (
        "\u2248 Converted into EUR at the exchange rates of Sep 23, 2026"
        " (European Central Bank, National Bank of Ukraine)."
    )


@pytest.mark.usefixtures("_english_afterwards")
def test_the_rates_note_in_russian() -> None:
    use_language("ru")

    note = rates_note(RATES, CurrencyCode("EUR"), display_locale(None))

    assert note.startswith("\u2248 Пересчитано в EUR по курсам на 23 сент. 2026")
    assert note.endswith("(Европейский центральный банк, Национальный банк Украины).")


def test_the_rates_note_needs_a_rate() -> None:
    with pytest.raises(ValueError):
        rates_note((), CurrencyCode("EUR"), display_locale(None))
