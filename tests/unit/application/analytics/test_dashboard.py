from collections import defaultdict
from collections.abc import Callable, Hashable
from datetime import UTC, date, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

import pytest

from mintflow.application.analytics import (
    BucketGranularity,
    BuildDashboard,
    CategoryTotal,
    CurrencyTotal,
    DailyTotal,
    Dashboard,
    DashboardDetail,
    DashboardPeriod,
    DashboardUserNotFound,
    MerchantTotal,
    resolve_currency,
    share_basis_points,
)
from mintflow.domain.capture import (
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.domain.user import Timezone, User

USD = CurrencyCode("USD")
EUR = CurrencyCode("EUR")
GBP = CurrencyCode("GBP")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
AUGUST = DashboardPeriod(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
K = TypeVar("K", bound=Hashable)
NAMES = {"groceries": "Groceries", "health": "Health", "uncategorized": "Uncategorized"}


class FakeAnalytics:
    """Computes the DASH-02 aggregates in memory over a list of active Expenses."""

    def __init__(self, expenses: list[Expense]) -> None:
        self.expenses = expenses

    def _select(
        self, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None = None
    ) -> list[Expense]:
        return [
            expense
            for expense in self.expenses
            if expense.owner_id == owner_id
            and expense.is_active
            and period.date_from <= expense.transaction_date.value <= period.date_to
            and (currency is None or expense.money.currency == currency)
        ]

    @staticmethod
    def _group(expenses: list[Expense], key: Callable[[Expense], K]) -> dict[K, list[Expense]]:
        groups: dict[K, list[Expense]] = defaultdict(list)
        for expense in expenses:
            groups[key(expense)].append(expense)
        return groups

    @staticmethod
    def _sum(expenses: list[Expense]) -> int:
        return sum(expense.money.minor_units for expense in expenses)

    def currency_totals(
        self, *, owner_id: UUID, period: DashboardPeriod
    ) -> tuple[CurrencyTotal, ...]:
        groups = self._group(self._select(owner_id, period), lambda e: e.money.currency)
        totals = [
            CurrencyTotal(currency=currency, total_minor_units=self._sum(rows), count=len(rows))
            for currency, rows in groups.items()
        ]
        return tuple(sorted(totals, key=lambda t: (-t.total_minor_units, t.currency.value)))

    def daily_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> tuple[DailyTotal, ...]:
        groups = self._group(
            self._select(owner_id, period, currency), lambda e: e.transaction_date.value
        )
        return tuple(
            DailyTotal(transaction_date=day, total_minor_units=self._sum(rows), count=len(rows))
            for day, rows in sorted(groups.items(), key=lambda item: item[0])
        )

    def category_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> tuple[CategoryTotal, ...]:
        groups = self._group(self._select(owner_id, period, currency), lambda e: e.category_key)
        # Deliberately unordered: the use case must rank on its own.
        return tuple(
            CategoryTotal(
                category_key=key,
                category_name=NAMES[key],
                total_minor_units=self._sum(rows),
                count=len(rows),
            )
            for key, rows in groups.items()
        )

    def merchant_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> tuple[MerchantTotal, ...]:
        groups = self._group(
            self._select(owner_id, period, currency),
            lambda e: e.merchant.value if e.merchant is not None else None,
        )
        return tuple(
            MerchantTotal(merchant=name, total_minor_units=self._sum(rows), count=len(rows))
            for name, rows in groups.items()
        )

    def largest_expense(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> Expense | None:
        rows = self._select(owner_id, period, currency)
        return max(
            rows,
            key=lambda e: (e.money.minor_units, e.transaction_date.value, e.created_at, e.id),
            default=None,
        )


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user

    def get(self, user_id: UUID) -> User | None:
        return self.user if self.user is not None and self.user.id == user_id else None


class Harness:
    def __init__(
        self,
        *,
        default_currency: CurrencyCode | None = None,
        timezone: str = "UTC",
        now: datetime = NOW,
    ) -> None:
        self.user = User.create(
            now=NOW, timezone=Timezone(timezone), default_currency=default_currency
        )
        self.expenses: list[Expense] = []
        self.use_case = BuildDashboard(
            analytics=FakeAnalytics(self.expenses),
            user_repository=FakeUsers(self.user),
            clock=lambda: now,
        )

    def add(
        self,
        amount: int,
        *,
        currency: CurrencyCode = USD,
        day: date = date(2026, 8, 10),
        category_key: str = "groceries",
        merchant: str | None = None,
        created_at: datetime = NOW,
    ) -> Expense:
        expense = Expense.create(
            owner_id=self.user.id,
            money=Money(minor_units=amount, currency=currency),
            transaction_date=TransactionDate(day),
            category_key=category_key,
            capture_draft_id=uuid4(),
            merchant=MerchantName(merchant) if merchant is not None else None,
            source=CaptureSource.WEB_MANUAL,
            now=created_at,
        )
        self.expenses.append(expense)
        return expense

    def build(
        self, *, period: DashboardPeriod | None = AUGUST, currency: CurrencyCode | None = None
    ) -> Dashboard:
        return self.use_case.execute(caller_id=self.user.id, period=period, currency=currency)

    def detail(
        self, *, period: DashboardPeriod | None = AUGUST, currency: CurrencyCode | None = None
    ) -> DashboardDetail:
        detail = self.build(period=period, currency=currency).detail
        assert detail is not None
        return detail


# --- pure helpers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("part", "whole", "expected"),
    [
        (1, 3, 3333),
        (2, 3, 6667),
        (1, 8, 1250),
        (1, 20_000, 0),  # 0.5 bp rounds half-even down to 0
        (3, 20_000, 2),  # 1.5 bp rounds half-even up to 2
        (5, 20_000, 2),  # 2.5 bp rounds half-even down to 2
        (-1, 4, -2500),
        (7, 7, 10_000),
        (5, 0, 0),
    ],
)
def test_share_basis_points_rounds_half_even_exactly(part: int, whole: int, expected: int) -> None:
    assert share_basis_points(part, whole) == expected


@pytest.mark.parametrize(
    ("requested", "present", "default", "expected"),
    [
        pytest.param(GBP, (USD, EUR), USD, (GBP, False), id="1 requested wins, even without data"),
        pytest.param(None, (EUR,), USD, (EUR, False), id="2 the only present currency"),
        pytest.param(None, (USD, EUR), EUR, (EUR, False), id="3 default among present"),
        pytest.param(None, (USD, EUR), GBP, (None, True), id="4 default not present"),
        pytest.param(None, (USD, EUR), None, (None, True), id="4 no default"),
        pytest.param(None, (), GBP, (GBP, False), id="no data uses default"),
        pytest.param(None, (), None, (None, False), id="no data and no default"),
    ],
)
def test_resolve_currency_follows_the_four_steps(
    requested: CurrencyCode | None,
    present: tuple[CurrencyCode, ...],
    default: CurrencyCode | None,
    expected: tuple[CurrencyCode | None, bool],
) -> None:
    assert resolve_currency(requested=requested, present=present, default=default) == expected


# --- currency selection ----------------------------------------------------------------------


def test_multi_currency_without_a_choice_requires_selection_and_keeps_separate_totals() -> None:
    harness = Harness(default_currency=GBP)
    harness.add(1000, currency=USD)
    harness.add(3000, currency=EUR)

    dashboard = harness.build()

    assert dashboard.currency is None
    assert dashboard.currency_selection_required is True
    assert dashboard.detail is None
    assert [(total.currency, total.total_minor_units) for total in dashboard.currencies] == [
        (EUR, 3000),
        (USD, 1000),
    ]


def test_default_currency_is_selected_when_present_and_never_combined() -> None:
    harness = Harness(default_currency=USD)
    harness.add(1000, currency=USD)
    harness.add(3000, currency=EUR)

    dashboard = harness.build()

    assert dashboard.currency == USD
    assert dashboard.detail is not None
    assert dashboard.detail.summary.total_minor_units == 1000
    assert dashboard.detail.summary.count == 1


def test_requested_currency_without_data_yields_zeroed_sections() -> None:
    harness = Harness()
    harness.add(1000, currency=USD)

    dashboard = harness.build(currency=GBP)

    assert dashboard.currency == GBP
    detail = dashboard.detail
    assert detail is not None
    assert detail.summary.total_minor_units == 0
    assert detail.categories == ()
    assert all(bucket.total_minor_units == 0 for bucket in detail.spending_over_time.buckets)
    assert detail.insights.largest_expense is None


@pytest.mark.parametrize("default_currency", [None, GBP])
def test_no_expenses_at_all_gives_empty_sections_not_missing_ones(
    default_currency: CurrencyCode | None,
) -> None:
    dashboard = Harness(default_currency=default_currency).build()

    assert dashboard.currency == default_currency
    assert dashboard.currency_selection_required is False
    assert dashboard.currencies == ()
    detail = dashboard.detail
    assert detail is not None
    assert detail.summary.total_minor_units == 0
    assert detail.summary.comparison is None
    assert len(detail.spending_over_time.buckets) == 31
    assert detail.top_merchants.merchants == ()
    assert detail.top_merchants.other is None
    assert detail.insights.largest_category is None


def test_default_period_is_the_current_month_in_the_users_timezone() -> None:
    # 22:30 UTC on 31 Aug is 1 Sep in Tokyo.
    harness = Harness(timezone="Asia/Tokyo", now=datetime(2026, 8, 31, 22, 30, tzinfo=UTC))

    dashboard = harness.build(period=None)

    assert dashboard.period == DashboardPeriod(
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)
    )


def test_unknown_user_is_a_dedicated_outcome() -> None:
    harness = Harness()

    with pytest.raises(DashboardUserNotFound):
        harness.use_case.execute(caller_id=uuid4())


# --- comparison ------------------------------------------------------------------------------


def test_comparison_uses_month_to_date_against_the_same_days_last_month() -> None:
    harness = Harness()  # today is 20 Aug
    harness.add(1000, day=date(2026, 8, 5))
    harness.add(9999, day=date(2026, 8, 21))  # tomorrow: in the period, not in the comparison
    harness.add(800, day=date(2026, 7, 20))
    harness.add(700, day=date(2026, 7, 21))  # outside 1-20 July

    comparison = harness.detail().summary.comparison

    assert comparison is not None
    assert comparison.current_period == DashboardPeriod(
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 20)
    )
    assert comparison.previous_period == DashboardPeriod(
        date_from=date(2026, 7, 1), date_to=date(2026, 7, 20)
    )
    assert comparison.current_total_minor_units == 1000
    assert comparison.previous_total_minor_units == 800
    assert comparison.change_minor_units == 200
    assert comparison.change_basis_points == 2500


def test_comparison_is_absent_without_previous_data_in_the_selected_currency() -> None:
    harness = Harness()
    harness.add(1000, day=date(2026, 8, 5))
    harness.add(800, currency=EUR, day=date(2026, 7, 5))

    assert harness.detail(currency=USD).summary.comparison is None


def test_comparison_reports_a_full_drop_when_the_current_window_is_empty() -> None:
    harness = Harness()
    harness.add(1000, day=date(2026, 8, 25))  # after today
    harness.add(400, day=date(2026, 7, 10))

    comparison = harness.detail().summary.comparison

    assert comparison is not None
    assert comparison.current_total_minor_units == 0
    assert comparison.change_minor_units == -400
    assert comparison.change_basis_points == -10_000


def test_future_period_has_no_comparison() -> None:
    harness = Harness()
    harness.add(1000, day=date(2026, 9, 1))
    september = DashboardPeriod(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))

    assert harness.detail(period=september).summary.comparison is None


# --- spending over time ----------------------------------------------------------------------


def test_daily_buckets_include_zero_days() -> None:
    harness = Harness()
    harness.add(1000, day=date(2026, 8, 3))
    harness.add(500, day=date(2026, 8, 3))
    harness.add(200, day=date(2026, 8, 31))

    over_time = harness.detail().spending_over_time

    assert over_time.granularity is BucketGranularity.DAY
    assert len(over_time.buckets) == 31
    by_day = {bucket.period.date_from: bucket for bucket in over_time.buckets}
    assert (by_day[date(2026, 8, 3)].total_minor_units, by_day[date(2026, 8, 3)].count) == (1500, 2)
    assert by_day[date(2026, 8, 31)].total_minor_units == 200
    assert by_day[date(2026, 8, 4)].total_minor_units == 0


def test_monthly_buckets_sum_their_days_and_keep_empty_months() -> None:
    harness = Harness()
    harness.add(1000, day=date(2026, 6, 15))
    harness.add(300, day=date(2026, 8, 1))
    harness.add(200, day=date(2026, 8, 20))
    period = DashboardPeriod(date_from=date(2026, 6, 10), date_to=date(2026, 8, 20))

    over_time = harness.detail(period=period).spending_over_time

    assert over_time.granularity is BucketGranularity.MONTH
    assert [
        (bucket.period.date_from, bucket.period.date_to, bucket.total_minor_units)
        for bucket in over_time.buckets
    ] == [
        (date(2026, 6, 10), date(2026, 6, 30), 1000),
        (date(2026, 7, 1), date(2026, 7, 31), 0),
        (date(2026, 8, 1), date(2026, 8, 20), 500),
    ]


# --- categories, merchants, insights ---------------------------------------------------------


def test_categories_are_ranked_with_shares_and_key_ties() -> None:
    harness = Harness()
    harness.add(1000, category_key="uncategorized")
    harness.add(1000, category_key="health")
    harness.add(1000, category_key="groceries")

    categories = harness.detail().categories

    assert [(c.category_key, c.share_basis_points) for c in categories] == [
        ("groceries", 3333),
        ("health", 3333),
        ("uncategorized", 3333),
    ]
    assert categories[0].category_name == "Groceries"


def test_largest_category_skips_uncategorized() -> None:
    harness = Harness()
    harness.add(5000, category_key="uncategorized")
    harness.add(1000, category_key="health")

    insights = harness.detail().insights

    assert insights.largest_category is not None
    assert insights.largest_category.category_key == "health"
    assert insights.largest_category.share_basis_points == 1667


def test_only_uncategorized_spending_has_no_largest_category() -> None:
    harness = Harness()
    harness.add(5000, category_key="uncategorized")

    assert harness.detail().insights.largest_category is None


def test_exactly_five_named_merchants_have_no_other_group() -> None:
    harness = Harness()
    for index, name in enumerate(["A", "B", "C", "D", "E"]):
        harness.add(1000 + index, merchant=name)

    top = harness.detail().top_merchants

    assert [merchant.merchant for merchant in top.merchants] == ["E", "D", "C", "B", "A"]
    assert top.other is None


def test_more_than_five_merchants_and_unnamed_expenses_form_other() -> None:
    harness = Harness()
    for name, amount in [("A", 600), ("B", 500), ("C", 500), ("D", 400), ("E", 300), ("F", 200)]:
        harness.add(amount, merchant=name)
    harness.add(1000)  # no merchant: never ranked, always in Other

    top = harness.detail().top_merchants

    assert [(m.merchant, m.total_minor_units) for m in top.merchants] == [
        ("A", 600),
        ("B", 500),
        ("C", 500),
        ("D", 400),
        ("E", 300),
    ]
    assert top.other is not None
    assert (top.other.merchant, top.other.total_minor_units, top.other.count) == (None, 1200, 2)
    assert top.other.share_basis_points == share_basis_points(1200, 3500)


def test_only_unnamed_expenses_are_all_other() -> None:
    harness = Harness()
    harness.add(1000)

    top = harness.detail().top_merchants

    assert top.merchants == ()
    assert top.other is not None
    assert top.other.share_basis_points == 10_000


def test_largest_expense_ties_break_by_date_then_creation() -> None:
    harness = Harness()
    harness.add(5000, day=date(2026, 8, 5), merchant="Early")
    harness.add(5000, day=date(2026, 8, 6), merchant="Later")
    winner = harness.add(
        5000, day=date(2026, 8, 6), created_at=NOW + timedelta(minutes=1), merchant=None
    )

    largest = harness.detail().insights.largest_expense

    assert largest is not None
    assert largest.expense_id == winner.id
    assert largest.merchant is None
    assert largest.money == Money(minor_units=5000, currency=USD)
    assert largest.transaction_date == date(2026, 8, 6)


def test_deleted_expenses_are_invisible_to_every_section() -> None:
    harness = Harness()
    kept = harness.add(100, merchant="Kept")
    deleted = harness.add(9000, merchant="Gone", category_key="health")
    harness.expenses[harness.expenses.index(deleted)] = deleted.delete(now=NOW)

    detail = harness.build().detail

    assert detail is not None
    assert detail.summary.total_minor_units == 100
    assert [c.category_key for c in detail.categories] == ["groceries"]
    assert [m.merchant for m in detail.top_merchants.merchants] == ["Kept"]
    assert detail.insights.largest_expense is not None
    assert detail.insights.largest_expense.expense_id == kept.id
