"""Assemble the dashboard for one user, period, and currency (docs/dashboard_design.md).

A converted dashboard shows every currency with a rate in one target currency
(docs/exchange_rates_design.md, X3 and X4): each currency group converts and rounds once, then
the groups add up. Currencies without a rate stay out of every converted figure and are listed
separately.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from typing import Final, Protocol
from uuid import UUID

from mintflow.application.analytics.aggregates import (
    AnalyticsRepository,
    CategoryTotal,
    CurrencyTotal,
    DailyTotal,
    MerchantTotal,
)
from mintflow.application.analytics.periods import (
    BucketGranularity,
    DashboardPeriod,
    chart_buckets,
    comparison_windows,
    default_period,
    local_today,
)
from mintflow.application.rates import ExchangeRate, ExchangeRates
from mintflow.domain.capture import UNCATEGORIZED_KEY, CurrencyCode, Expense, Money
from mintflow.domain.user import User

TOP_MERCHANT_COUNT: Final = 5
_BASIS_POINTS: Final = 10_000


class DashboardUserNotFound(Exception):
    """The caller has no User record."""


@dataclass(frozen=True, slots=True)
class Comparison:
    """The part of the period up to today against the previous comparable window (D4)."""

    current_period: DashboardPeriod
    current_total_minor_units: int
    previous_period: DashboardPeriod
    previous_total_minor_units: int
    change_minor_units: int
    change_basis_points: int | None


@dataclass(frozen=True, slots=True)
class Summary:
    total_minor_units: int
    count: int
    comparison: Comparison | None


@dataclass(frozen=True, slots=True)
class TimeBucket:
    period: DashboardPeriod
    total_minor_units: int
    count: int


@dataclass(frozen=True, slots=True)
class SpendingOverTime:
    granularity: BucketGranularity
    buckets: tuple[TimeBucket, ...]


@dataclass(frozen=True, slots=True)
class CategorySpending:
    category_key: str
    category_name: str
    total_minor_units: int
    count: int
    share_basis_points: int


@dataclass(frozen=True, slots=True)
class MerchantSpending:
    """``merchant`` is None only for the "Other" group."""

    merchant: str | None
    total_minor_units: int
    count: int
    share_basis_points: int


@dataclass(frozen=True, slots=True)
class TopMerchants:
    merchants: tuple[MerchantSpending, ...]
    other: MerchantSpending | None


@dataclass(frozen=True, slots=True)
class LargestExpense:
    """``converted_minor_units`` is the amount in the dashboard currency when converted."""

    expense_id: UUID
    money: Money
    merchant: str | None
    transaction_date: date
    converted_minor_units: int | None = None


@dataclass(frozen=True, slots=True)
class Insights:
    largest_category: CategorySpending | None
    largest_expense: LargestExpense | None


@dataclass(frozen=True, slots=True)
class DashboardDetail:
    summary: Summary
    spending_over_time: SpendingOverTime
    categories: tuple[CategorySpending, ...]
    top_merchants: TopMerchants
    insights: Insights


@dataclass(frozen=True, slots=True)
class ConvertTo:
    """Ask for the dashboard in ``currency`` at the ``rates`` snapshot."""

    currency: CurrencyCode
    rates: ExchangeRates


@dataclass(frozen=True, slots=True)
class Conversion:
    """The rates a converted dashboard used, and the currencies it could not convert."""

    rates: tuple[ExchangeRate, ...]
    unconverted: tuple[CurrencyTotal, ...]


@dataclass(frozen=True, slots=True)
class Dashboard:
    """``detail`` is None exactly when ``currency_selection_required`` is True (D3).

    ``conversion`` is set exactly when the dashboard was converted; ``currencies`` always holds
    the original per-currency totals.
    """

    period: DashboardPeriod
    currencies: tuple[CurrencyTotal, ...]
    currency: CurrencyCode | None
    currency_selection_required: bool
    detail: DashboardDetail | None
    conversion: Conversion | None = None


class UserRepository(Protocol):
    def get(self, user_id: UUID) -> User | None: ...


def share_basis_points(part: int, whole: int) -> int:
    """``part / whole`` in basis points, rounded half-even, exact (no floating point)."""
    if whole <= 0:
        return 0
    return round(Fraction(part * _BASIS_POINTS, whole))


def resolve_currency(
    *,
    requested: CurrencyCode | None,
    present: Iterable[CurrencyCode],
    default: CurrencyCode | None,
) -> tuple[CurrencyCode | None, bool]:
    """The D3 currency selection: (selected currency, selection required)."""
    if requested is not None:
        return requested, False
    present = tuple(present)
    if len(present) == 1:
        return present[0], False
    if not present:
        return default, False
    if default is not None and default in present:
        return default, False
    return None, True


def _bucket_totals(period: DashboardPeriod, daily: tuple[DailyTotal, ...]) -> SpendingOverTime:
    layout = chart_buckets(period)
    buckets = []
    for bucket in layout.buckets:
        inside = [
            day for day in daily if bucket.date_from <= day.transaction_date <= bucket.date_to
        ]
        buckets.append(
            TimeBucket(
                period=bucket,
                total_minor_units=sum(day.total_minor_units for day in inside),
                count=sum(day.count for day in inside),
            )
        )
    return SpendingOverTime(granularity=layout.granularity, buckets=tuple(buckets))


def _rank_categories(
    totals: tuple[CategoryTotal, ...], grand_total: int
) -> tuple[CategorySpending, ...]:
    ranked = sorted(totals, key=lambda total: (-total.total_minor_units, total.category_key))
    return tuple(
        CategorySpending(
            category_key=total.category_key,
            category_name=total.category_name,
            total_minor_units=total.total_minor_units,
            count=total.count,
            share_basis_points=share_basis_points(total.total_minor_units, grand_total),
        )
        for total in ranked
    )


def _top_merchants(totals: tuple[MerchantTotal, ...], grand_total: int) -> TopMerchants:
    named = sorted(
        (total for total in totals if total.merchant is not None),
        key=lambda total: (-total.total_minor_units, total.merchant),
    )
    top, rest = named[:TOP_MERCHANT_COUNT], named[TOP_MERCHANT_COUNT:]
    rest += [total for total in totals if total.merchant is None]
    other_total = sum(total.total_minor_units for total in rest)
    other_count = sum(total.count for total in rest)
    return TopMerchants(
        merchants=tuple(
            MerchantSpending(
                merchant=total.merchant,
                total_minor_units=total.total_minor_units,
                count=total.count,
                share_basis_points=share_basis_points(total.total_minor_units, grand_total),
            )
            for total in top
        ),
        other=(
            MerchantSpending(
                merchant=None,
                total_minor_units=other_total,
                count=other_count,
                share_basis_points=share_basis_points(other_total, grand_total),
            )
            if other_count
            else None
        ),
    )


class _Converter:
    """Converts into one target currency and remembers which rates it used."""

    def __init__(self, convert_to: ConvertTo) -> None:
        self.target = convert_to.currency
        self._rates = convert_to.rates
        self._used: set[str] = set()

    def can_convert(self, currency: CurrencyCode) -> bool:
        return self._rates.can_convert(currency, self.target)

    def convert(self, minor_units: int, currency: CurrencyCode) -> int:
        converted = self._rates.convert(minor_units, currency, self.target)
        if converted is None:
            raise ValueError(f"no rate for {currency.value}")
        if currency != self.target:
            self._used.update((currency.value, self.target.value))
        return converted

    def used_rates(self) -> tuple[ExchangeRate, ...]:
        # The euro is the base and has no stored rate.
        return tuple(
            self._rates.rates[code] for code in sorted(self._used) if code in self._rates.rates
        )


def _converted_buckets(
    period: DashboardPeriod, daily: tuple[DailyTotal, ...], converter: _Converter
) -> SpendingOverTime:
    """Each currency's bucket total converts once; the converted totals add up per bucket."""
    by_currency: dict[CurrencyCode, list[DailyTotal]] = defaultdict(list)
    for day in daily:
        by_currency[day.currency].append(day)
    combined = _bucket_totals(period, ())
    buckets = list(combined.buckets)
    for currency, days in by_currency.items():
        for index, bucket in enumerate(_bucket_totals(period, tuple(days)).buckets):
            if bucket.count:
                buckets[index] = TimeBucket(
                    period=bucket.period,
                    total_minor_units=buckets[index].total_minor_units
                    + converter.convert(bucket.total_minor_units, currency),
                    count=buckets[index].count + bucket.count,
                )
    return SpendingOverTime(granularity=combined.granularity, buckets=tuple(buckets))


def _converted_categories(
    totals: tuple[CategoryTotal, ...], converter: _Converter
) -> tuple[CategoryTotal, ...]:
    combined: dict[str, CategoryTotal] = {}
    for total in totals:
        converted = converter.convert(total.total_minor_units, total.currency)
        previous = combined.get(total.category_key)
        combined[total.category_key] = CategoryTotal(
            currency=converter.target,
            category_key=total.category_key,
            category_name=total.category_name,
            total_minor_units=converted + (previous.total_minor_units if previous else 0),
            count=total.count + (previous.count if previous else 0),
        )
    return tuple(combined.values())


def _converted_merchants(
    totals: tuple[MerchantTotal, ...], converter: _Converter
) -> tuple[MerchantTotal, ...]:
    combined: dict[str | None, MerchantTotal] = {}
    for total in totals:
        converted = converter.convert(total.total_minor_units, total.currency)
        previous = combined.get(total.merchant)
        combined[total.merchant] = MerchantTotal(
            currency=converter.target,
            merchant=total.merchant,
            total_minor_units=converted + (previous.total_minor_units if previous else 0),
            count=total.count + (previous.count if previous else 0),
        )
    return tuple(combined.values())


def _largest_expense(expense: Expense, converted_minor_units: int | None = None) -> LargestExpense:
    return LargestExpense(
        expense_id=expense.id,
        money=expense.money,
        merchant=expense.merchant.value if expense.merchant is not None else None,
        transaction_date=expense.transaction_date.value,
        converted_minor_units=converted_minor_units,
    )


def _assemble_detail(
    *,
    total: int,
    count: int,
    comparison: Comparison | None,
    spending_over_time: SpendingOverTime,
    category_totals: tuple[CategoryTotal, ...],
    merchant_totals: tuple[MerchantTotal, ...],
    largest: LargestExpense | None,
) -> DashboardDetail:
    categories = _rank_categories(category_totals, total)
    return DashboardDetail(
        summary=Summary(total_minor_units=total, count=count, comparison=comparison),
        spending_over_time=spending_over_time,
        categories=categories,
        top_merchants=_top_merchants(merchant_totals, total),
        insights=Insights(
            largest_category=next(
                (category for category in categories if category.category_key != UNCATEGORIZED_KEY),
                None,
            ),
            largest_expense=largest,
        ),
    )


def _empty_detail(period: DashboardPeriod) -> DashboardDetail:
    return DashboardDetail(
        summary=Summary(total_minor_units=0, count=0, comparison=None),
        spending_over_time=_bucket_totals(period, ()),
        categories=(),
        top_merchants=TopMerchants(merchants=(), other=None),
        insights=Insights(largest_category=None, largest_expense=None),
    )


class BuildDashboard:
    def __init__(
        self,
        *,
        analytics: AnalyticsRepository,
        user_repository: UserRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._analytics = analytics
        self._user_repository = user_repository
        self._clock = clock

    def execute(
        self,
        *,
        caller_id: UUID,
        period: DashboardPeriod | None = None,
        currency: CurrencyCode | None = None,
        convert_to: ConvertTo | None = None,
    ) -> Dashboard:
        """``currency`` picks one currency's own dashboard; ``convert_to`` converts them all."""
        if currency is not None and convert_to is not None:
            raise ValueError("a dashboard is either in one currency or converted, not both")
        user = self._user_repository.get(caller_id)
        if user is None:
            raise DashboardUserNotFound("user not found")
        now = self._clock()
        if period is None:
            period = default_period(now=now, timezone=user.timezone)

        currencies = self._analytics.currency_totals(owner_id=caller_id, period=period)
        today = local_today(now=now, timezone=user.timezone)
        if convert_to is not None:
            converter = _Converter(convert_to)
            converted_detail = self._converted_detail(
                caller_id, period, converter, currencies, today=today
            )
            return Dashboard(
                period=period,
                currencies=currencies,
                currency=converter.target,
                currency_selection_required=False,
                detail=converted_detail,
                conversion=Conversion(
                    rates=converter.used_rates(),
                    unconverted=tuple(
                        total for total in currencies if not converter.can_convert(total.currency)
                    ),
                ),
            )

        selected, selection_required = resolve_currency(
            requested=currency,
            present=(total.currency for total in currencies),
            default=user.default_currency,
        )
        if selection_required:
            detail = None
        elif selected is None:
            detail = _empty_detail(period)
        else:
            detail = self._detail(caller_id, period, selected, currencies, today=today)
        return Dashboard(
            period=period,
            currencies=currencies,
            currency=selected,
            currency_selection_required=selection_required,
            detail=detail,
        )

    def _detail(
        self,
        owner_id: UUID,
        period: DashboardPeriod,
        currency: CurrencyCode,
        currencies: tuple[CurrencyTotal, ...],
        *,
        today: date,
    ) -> DashboardDetail:
        selected_total = next((total for total in currencies if total.currency == currency), None)
        largest = self._analytics.largest_expense(
            owner_id=owner_id, period=period, currency=currency
        )

        def window_total(window: DashboardPeriod) -> tuple[int, int]:
            found = self._total_in(owner_id, window, currency)
            return (found.total_minor_units, found.count) if found is not None else (0, 0)

        return _assemble_detail(
            total=selected_total.total_minor_units if selected_total is not None else 0,
            count=selected_total.count if selected_total is not None else 0,
            comparison=self._comparison(period, window_total, today=today),
            spending_over_time=_bucket_totals(
                period,
                self._analytics.daily_totals(owner_id=owner_id, period=period, currency=currency),
            ),
            category_totals=self._analytics.category_totals(
                owner_id=owner_id, period=period, currency=currency
            ),
            merchant_totals=self._analytics.merchant_totals(
                owner_id=owner_id, period=period, currency=currency
            ),
            largest=_largest_expense(largest) if largest is not None else None,
        )

    def _converted_detail(
        self,
        owner_id: UUID,
        period: DashboardPeriod,
        converter: _Converter,
        currencies: tuple[CurrencyTotal, ...],
        *,
        today: date,
    ) -> DashboardDetail:
        def convertible[T: (CurrencyTotal, DailyTotal, CategoryTotal, MerchantTotal)](
            rows: tuple[T, ...],
        ) -> tuple[T, ...]:
            return tuple(row for row in rows if converter.can_convert(row.currency))

        def converted_total(totals: tuple[CurrencyTotal, ...]) -> tuple[int, int]:
            return (
                sum(converter.convert(total.total_minor_units, total.currency) for total in totals),
                sum(total.count for total in totals),
            )

        def window_total(window: DashboardPeriod) -> tuple[int, int]:
            return converted_total(
                convertible(self._analytics.currency_totals(owner_id=owner_id, period=window))
            )

        total, count = converted_total(convertible(currencies))
        return _assemble_detail(
            total=total,
            count=count,
            comparison=self._comparison(period, window_total, today=today),
            spending_over_time=_converted_buckets(
                period,
                convertible(
                    self._analytics.daily_totals(owner_id=owner_id, period=period, currency=None)
                ),
                converter,
            ),
            category_totals=_converted_categories(
                convertible(
                    self._analytics.category_totals(owner_id=owner_id, period=period, currency=None)
                ),
                converter,
            ),
            merchant_totals=_converted_merchants(
                convertible(
                    self._analytics.merchant_totals(owner_id=owner_id, period=period, currency=None)
                ),
                converter,
            ),
            largest=self._converted_largest(owner_id, period, convertible(currencies), converter),
        )

    def _converted_largest(
        self,
        owner_id: UUID,
        period: DashboardPeriod,
        currencies: tuple[CurrencyTotal, ...],
        converter: _Converter,
    ) -> LargestExpense | None:
        """The largest converted amount; ties break as in the repository (later date, then later
        creation, then id)."""
        candidates = []
        for total in currencies:
            expense = self._analytics.largest_expense(
                owner_id=owner_id, period=period, currency=total.currency
            )
            if expense is not None:
                converted = converter.convert(expense.money.minor_units, expense.money.currency)
                candidates.append((converted, expense))
        if not candidates:
            return None
        converted, expense = max(
            candidates,
            key=lambda candidate: (
                candidate[0],
                candidate[1].transaction_date.value,
                candidate[1].created_at,
                candidate[1].id,
            ),
        )
        return _largest_expense(expense, converted)

    def _comparison(
        self,
        period: DashboardPeriod,
        window_total: Callable[[DashboardPeriod], tuple[int, int]],
        *,
        today: date,
    ) -> Comparison | None:
        """``window_total`` gives (total in minor units, count) for a window."""
        windows = comparison_windows(period, today=today)
        if windows is None:
            return None
        previous_total, previous_count = window_total(windows.previous)
        if previous_count == 0:
            return None
        current_total, _ = window_total(windows.current)
        change = current_total - previous_total
        return Comparison(
            current_period=windows.current,
            current_total_minor_units=current_total,
            previous_period=windows.previous,
            previous_total_minor_units=previous_total,
            change_minor_units=change,
            change_basis_points=(
                share_basis_points(change, previous_total) if previous_total > 0 else None
            ),
        )

    def _total_in(
        self, owner_id: UUID, window: DashboardPeriod, currency: CurrencyCode
    ) -> CurrencyTotal | None:
        return next(
            (
                total
                for total in self._analytics.currency_totals(owner_id=owner_id, period=window)
                if total.currency == currency
            ),
            None,
        )
