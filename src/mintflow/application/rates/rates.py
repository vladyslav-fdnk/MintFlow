"""Exchange rates for showing totals in one currency (docs/exchange_rates_design.md).

Rates are units of a currency per one euro, the ECB's base. Conversion never guesses: a currency
without a rate converts to nothing, and the caller shows it separately. Expenses are never
converted in storage; only displayed totals are.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final, Protocol

from mintflow.domain.capture import CurrencyCode, supported_currency_codes

EURO: Final = CurrencyCode("EUR")


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    currency: CurrencyCode
    units_per_eur: Decimal
    rate_date: date
    source: str

    def __post_init__(self) -> None:
        if not self.units_per_eur.is_finite() or self.units_per_eur <= 0:
            raise ValueError("a rate must be a positive number")


@dataclass(frozen=True, slots=True)
class ExchangeRates:
    """The latest known rate per currency; the euro itself needs none."""

    rates: Mapping[str, ExchangeRate] = field(default_factory=dict)

    def _units_per_eur(self, currency: CurrencyCode) -> Decimal | None:
        if currency == EURO:
            return Decimal(1)
        rate = self.rates.get(currency.value)
        return rate.units_per_eur if rate is not None else None

    def has_rate(self, currency: CurrencyCode) -> bool:
        return self._units_per_eur(currency) is not None

    def can_convert(self, source: CurrencyCode, target: CurrencyCode) -> bool:
        return source == target or (
            self._units_per_eur(source) is not None and self._units_per_eur(target) is not None
        )

    def convert(self, minor_units: int, source: CurrencyCode, target: CurrencyCode) -> int | None:
        """``minor_units`` of ``source`` in minor units of ``target``, rounded half-even."""
        if source == target:
            return minor_units
        source_rate = self._units_per_eur(source)
        target_rate = self._units_per_eur(target)
        if source_rate is None or target_rate is None:
            return None
        major = Decimal(minor_units).scaleb(-source.minor_unit_exponent)
        converted = (major / source_rate * target_rate).scaleb(target.minor_unit_exponent)
        return int(converted.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))

    @property
    def oldest_date(self) -> date | None:
        return min((rate.rate_date for rate in self.rates.values()), default=None)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({rate.source for rate in self.rates.values()}))


class ExchangeRateSourceFailed(Exception):
    """A source could not be fetched or its response was not understood."""


class ExchangeRateSource(Protocol):
    name: str

    def fetch(self) -> Sequence[ExchangeRate]:
        """Raises ExchangeRateSourceFailed."""
        ...


class ExchangeRateRepository(Protocol):
    def save(self, rates: Sequence[ExchangeRate], *, fetched_at: datetime) -> None: ...

    def latest(self) -> ExchangeRates: ...


@dataclass(frozen=True, slots=True)
class RefreshResult:
    saved: tuple[str, ...]
    failed_sources: tuple[str, ...]


class RefreshExchangeRates:
    """Fetch every source in priority order and store the latest rate per catalogue currency.

    An earlier source wins for a currency both publish. A failed source changes nothing it would
    have provided, so previously stored rates stay; the result names it.
    """

    def __init__(
        self,
        *,
        sources: Sequence[ExchangeRateSource],
        repository: ExchangeRateRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources = sources
        self._repository = repository
        self._clock = clock

    def execute(self) -> RefreshResult:
        wanted = set(supported_currency_codes()) - {EURO.value}
        chosen: dict[str, ExchangeRate] = {}
        failed: list[str] = []
        for source in self._sources:
            try:
                rates = source.fetch()
            except ExchangeRateSourceFailed:
                failed.append(source.name)
                continue
            for rate in rates:
                code = rate.currency.value
                if code in wanted and code not in chosen:
                    chosen[code] = rate
        if chosen:
            self._repository.save(list(chosen.values()), fetched_at=self._clock())
        return RefreshResult(saved=tuple(sorted(chosen)), failed_sources=tuple(failed))
