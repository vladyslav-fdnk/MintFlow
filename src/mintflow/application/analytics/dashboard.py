"""Assemble the dashboard for one user, period, and currency (docs/dashboard_design.md)."""

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
from mintflow.domain.capture import UNCATEGORIZED_KEY, CurrencyCode, Money
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
    expense_id: UUID
    money: Money
    merchant: str | None
    transaction_date: date


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
class Dashboard:
    """``detail`` is None exactly when ``currency_selection_required`` is True (D3)."""

    period: DashboardPeriod
    currencies: tuple[CurrencyTotal, ...]
    currency: CurrencyCode | None
    currency_selection_required: bool
    detail: DashboardDetail | None


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
    ) -> Dashboard:
        user = self._user_repository.get(caller_id)
        if user is None:
            raise DashboardUserNotFound("user not found")
        now = self._clock()
        if period is None:
            period = default_period(now=now, timezone=user.timezone)

        currencies = self._analytics.currency_totals(owner_id=caller_id, period=period)
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
            today = local_today(now=now, timezone=user.timezone)
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
        total = selected_total.total_minor_units if selected_total is not None else 0
        count = selected_total.count if selected_total is not None else 0

        categories = _rank_categories(
            self._analytics.category_totals(owner_id=owner_id, period=period, currency=currency),
            total,
        )
        largest = self._analytics.largest_expense(
            owner_id=owner_id, period=period, currency=currency
        )
        return DashboardDetail(
            summary=Summary(
                total_minor_units=total,
                count=count,
                comparison=self._comparison(owner_id, period, currency, today=today),
            ),
            spending_over_time=_bucket_totals(
                period,
                self._analytics.daily_totals(owner_id=owner_id, period=period, currency=currency),
            ),
            categories=categories,
            top_merchants=_top_merchants(
                self._analytics.merchant_totals(
                    owner_id=owner_id, period=period, currency=currency
                ),
                total,
            ),
            insights=Insights(
                largest_category=next(
                    (
                        category
                        for category in categories
                        if category.category_key != UNCATEGORIZED_KEY
                    ),
                    None,
                ),
                largest_expense=(
                    LargestExpense(
                        expense_id=largest.id,
                        money=largest.money,
                        merchant=largest.merchant.value if largest.merchant is not None else None,
                        transaction_date=largest.transaction_date.value,
                    )
                    if largest is not None
                    else None
                ),
            ),
        )

    def _comparison(
        self, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode, *, today: date
    ) -> Comparison | None:
        windows = comparison_windows(period, today=today)
        if windows is None:
            return None
        previous = self._total_in(owner_id, windows.previous, currency)
        if previous is None or previous.count == 0:
            return None
        current = self._total_in(owner_id, windows.current, currency)
        current_total = current.total_minor_units if current is not None else 0
        change = current_total - previous.total_minor_units
        return Comparison(
            current_period=windows.current,
            current_total_minor_units=current_total,
            previous_period=windows.previous,
            previous_total_minor_units=previous.total_minor_units,
            change_minor_units=change,
            change_basis_points=(
                share_basis_points(change, previous.total_minor_units)
                if previous.total_minor_units > 0
                else None
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
