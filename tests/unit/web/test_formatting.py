from datetime import date

import pytest

from mintflow.domain.capture import CurrencyCode, Money
from mintflow.domain.user import Locale
from mintflow.web.formatting import display_locale, format_date, format_money

NBSP = " "
NARROW_NBSP = " "


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
