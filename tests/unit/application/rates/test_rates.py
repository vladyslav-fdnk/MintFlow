from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from mintflow.application.rates import (
    ExchangeRate,
    ExchangeRates,
    ExchangeRateSourceFailed,
    RefreshExchangeRates,
)
from mintflow.domain.capture import CurrencyCode

DAY = date(2026, 9, 23)
EUR, USD, PLN, JPY, BHD, UAH, GBP = (
    CurrencyCode(code) for code in ("EUR", "USD", "PLN", "JPY", "BHD", "UAH", "GBP")
)


def _rate(currency: CurrencyCode, units: str, source: str = "ECB") -> ExchangeRate:
    return ExchangeRate(currency, Decimal(units), DAY, source)


RATES = ExchangeRates(
    {
        "USD": _rate(USD, "1.25"),
        "PLN": _rate(PLN, "4.00"),
        "JPY": _rate(JPY, "160"),
        "BHD": _rate(BHD, "0.47"),
    }
)


@pytest.mark.parametrize(
    ("minor", "source", "target", "expected"),
    [
        (1_000, EUR, EUR, 1_000),
        (1_000, EUR, USD, 1_250),  # 10.00 EUR = 12.50 USD
        (1_250, USD, EUR, 1_000),
        (1_000, USD, PLN, 3_200),  # through the euro: 10 / 1.25 * 4
        (1_000, EUR, JPY, 1_600),  # JPY has no minor units: 10.00 EUR = 1,600 JPY
        (1_600, JPY, EUR, 1_000),
        (1_000, EUR, BHD, 4_700),  # three decimals: 4.700 BHD
    ],
)
def test_conversion_goes_through_the_euro_and_respects_minor_units(
    minor: int, source: CurrencyCode, target: CurrencyCode, expected: int
) -> None:
    assert RATES.convert(minor, source, target) == expected


def test_rounding_is_half_even() -> None:
    halves = ExchangeRates({"PLN": _rate(PLN, "0.5")})

    # Half a cent exactly: 0.125 PLN rounds down to the even 12, 0.135 PLN up to the even 14.
    assert halves.convert(25, EUR, PLN) == 12
    assert halves.convert(27, EUR, PLN) == 14


def test_a_currency_without_a_rate_is_never_guessed() -> None:
    assert RATES.convert(1_000, UAH, EUR) is None
    assert RATES.convert(1_000, EUR, UAH) is None
    assert not RATES.can_convert(UAH, EUR)
    assert RATES.can_convert(UAH, UAH) and RATES.can_convert(USD, PLN)


def test_the_snapshot_reports_its_age_and_sources() -> None:
    mixed = ExchangeRates(
        {
            "USD": _rate(USD, "1.2"),
            "UAH": ExchangeRate(UAH, Decimal("51"), date(2026, 9, 24), "NBU"),
        }
    )

    assert mixed.oldest_date == DAY
    assert mixed.sources == ("ECB", "NBU")
    assert ExchangeRates().oldest_date is None


@pytest.mark.parametrize("units", ["0", "-1", "NaN", "Infinity"])
def test_a_rate_must_be_a_positive_number(units: str) -> None:
    with pytest.raises(ValueError):
        _rate(USD, units)


class FakeSource:
    def __init__(self, name: str, rates: list[ExchangeRate] | None) -> None:
        self.name = name
        self._rates = rates

    def fetch(self) -> list[ExchangeRate]:
        if self._rates is None:
            raise ExchangeRateSourceFailed(f"{self.name}: down")
        return self._rates


class FakeRepository:
    def __init__(self) -> None:
        self.saved: list[list[ExchangeRate]] = []

    def save(self, rates: Sequence[ExchangeRate], *, fetched_at: datetime) -> None:
        self.saved.append(list(rates))

    def latest(self) -> ExchangeRates:
        raise AssertionError("not used")


NOW = datetime(2026, 9, 24, 17, 0, tzinfo=UTC)


def test_the_first_source_wins_and_later_ones_fill_gaps() -> None:
    repository = FakeRepository()
    ecb = FakeSource("ECB", [_rate(USD, "1.14"), _rate(GBP, "0.86")])
    nbu = FakeSource(
        "NBU",
        [_rate(UAH, "51.19", "NBU"), _rate(USD, "1.141099", "NBU")],
    )

    result = RefreshExchangeRates(
        sources=[ecb, nbu], repository=repository, clock=lambda: NOW
    ).execute()

    assert result.saved == ("GBP", "UAH", "USD")
    assert result.failed_sources == ()
    [saved] = repository.saved
    assert {rate.currency.value: rate.source for rate in saved} == {
        "USD": "ECB",
        "GBP": "ECB",
        "UAH": "NBU",
    }


def test_a_failed_source_is_reported_and_the_others_still_save() -> None:
    repository = FakeRepository()

    result = RefreshExchangeRates(
        sources=[FakeSource("ECB", None), FakeSource("NBU", [_rate(UAH, "51", "NBU")])],
        repository=repository,
        clock=lambda: NOW,
    ).execute()

    assert (result.saved, result.failed_sources) == (("UAH",), ("ECB",))
    assert len(repository.saved) == 1


def test_nothing_is_written_when_every_source_failed() -> None:
    repository = FakeRepository()

    result = RefreshExchangeRates(
        sources=[FakeSource("ECB", None), FakeSource("NBU", None)],
        repository=repository,
        clock=lambda: NOW,
    ).execute()

    assert (result.saved, result.failed_sources) == ((), ("ECB", "NBU"))
    assert repository.saved == []
