from datetime import UTC, date, datetime, timedelta

import pytest

from mintflow.application.analytics import (
    BucketGranularity,
    DashboardPeriod,
    chart_buckets,
    comparison_windows,
    default_period,
    local_today,
)
from mintflow.domain.user import Timezone


def _period(date_from: str, date_to: str) -> DashboardPeriod:
    return DashboardPeriod(
        date_from=date.fromisoformat(date_from), date_to=date.fromisoformat(date_to)
    )


# --- DashboardPeriod -------------------------------------------------------------------------


def test_period_counts_days_inclusively() -> None:
    assert _period("2026-08-01", "2026-08-01").days == 1
    assert _period("2026-08-01", "2026-08-31").days == 31


def test_period_accepts_exactly_366_days_and_rejects_367() -> None:
    assert _period("2028-01-01", "2028-12-31").days == 366
    with pytest.raises(ValueError, match="366"):
        _period("2026-01-01", "2027-01-02")


def test_period_rejects_an_inverted_range() -> None:
    with pytest.raises(ValueError, match="date_from"):
        _period("2026-08-02", "2026-08-01")


@pytest.mark.parametrize(
    ("date_from", "date_to"),
    [("1999-12-31", "2000-01-05"), ("2100-12-30", "2101-01-01")],
)
def test_period_rejects_years_outside_the_supported_range(date_from: str, date_to: str) -> None:
    with pytest.raises(ValueError, match="years"):
        _period(date_from, date_to)


# --- default_period --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "timezone", "expected"),
    [
        pytest.param(
            datetime(2026, 8, 15, 12, 0, tzinfo=UTC), "UTC", ("2026-08-01", "2026-08-31"), id="aug"
        ),
        pytest.param(
            datetime(2026, 9, 3, 9, 0, tzinfo=UTC), "UTC", ("2026-09-01", "2026-09-30"), id="sep"
        ),
        pytest.param(
            datetime(2028, 2, 10, 0, 0, tzinfo=UTC), "UTC", ("2028-02-01", "2028-02-29"), id="leap"
        ),
        pytest.param(
            datetime(2026, 2, 10, 0, 0, tzinfo=UTC), "UTC", ("2026-02-01", "2026-02-28"), id="feb"
        ),
        pytest.param(
            # 22:30 UTC on 31 Aug is already 1 Sep in Tokyo.
            datetime(2026, 8, 31, 22, 30, tzinfo=UTC),
            "Asia/Tokyo",
            ("2026-09-01", "2026-09-30"),
            id="tokyo ahead of utc",
        ),
        pytest.param(
            # 03:00 UTC on 1 Sep is still 31 Aug in Los Angeles.
            datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
            "America/Los_Angeles",
            ("2026-08-01", "2026-08-31"),
            id="los angeles behind utc",
        ),
        pytest.param(
            datetime(2026, 12, 31, 23, 0, tzinfo=UTC),
            "Europe/Berlin",
            ("2027-01-01", "2027-01-31"),
            id="new year in berlin",
        ),
    ],
)
def test_default_period_is_the_current_local_calendar_month(
    now: datetime, timezone: str, expected: tuple[str, str]
) -> None:
    assert default_period(now=now, timezone=Timezone(timezone)) == _period(*expected)


def test_local_today_requires_an_aware_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        local_today(now=datetime(2026, 8, 15, 12, 0), timezone=Timezone("UTC"))


# --- chart_buckets ---------------------------------------------------------------------------


def _assert_buckets_tile(period: DashboardPeriod, buckets: tuple[DashboardPeriod, ...]) -> None:
    assert buckets[0].date_from == period.date_from
    assert buckets[-1].date_to == period.date_to
    for earlier, later in zip(buckets, buckets[1:], strict=False):
        assert later.date_from == earlier.date_to + timedelta(days=1)


def test_single_day_period_has_one_daily_bucket() -> None:
    period = _period("2026-08-15", "2026-08-15")

    result = chart_buckets(period)

    assert result.granularity is BucketGranularity.DAY
    assert result.buckets == (period,)


def test_exactly_31_days_is_daily() -> None:
    period = _period("2026-08-01", "2026-08-31")

    result = chart_buckets(period)

    assert result.granularity is BucketGranularity.DAY
    assert len(result.buckets) == 31
    assert all(bucket.days == 1 for bucket in result.buckets)
    _assert_buckets_tile(period, result.buckets)


def test_32_days_is_monthly_with_clipped_edge_months() -> None:
    period = _period("2026-07-31", "2026-08-31")

    result = chart_buckets(period)

    assert result.granularity is BucketGranularity.MONTH
    assert result.buckets == (
        _period("2026-07-31", "2026-07-31"),
        _period("2026-08-01", "2026-08-31"),
    )


def test_monthly_buckets_cross_a_year_and_a_leap_february() -> None:
    period = _period("2027-11-15", "2028-03-10")

    result = chart_buckets(period)

    assert result.buckets == (
        _period("2027-11-15", "2027-11-30"),
        _period("2027-12-01", "2027-12-31"),
        _period("2028-01-01", "2028-01-31"),
        _period("2028-02-01", "2028-02-29"),
        _period("2028-03-01", "2028-03-10"),
    )
    _assert_buckets_tile(period, result.buckets)


def test_a_366_day_period_has_at_most_13_monthly_buckets() -> None:
    period = _period("2027-01-15", "2028-01-15")

    result = chart_buckets(period)

    assert len(result.buckets) == 13
    _assert_buckets_tile(period, result.buckets)


# --- comparison_windows ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "today", "current", "previous"),
    [
        pytest.param(
            ("2026-08-01", "2026-08-31"),
            "2026-08-23",
            ("2026-08-01", "2026-08-23"),
            ("2026-07-01", "2026-07-23"),
            id="month to date vs same days last month",
        ),
        pytest.param(
            ("2026-08-01", "2026-08-31"),
            "2026-09-10",
            ("2026-08-01", "2026-08-31"),
            ("2026-07-01", "2026-07-31"),
            id="complete past month",
        ),
        pytest.param(
            ("2026-10-01", "2026-10-31"),
            "2026-11-01",
            ("2026-10-01", "2026-10-31"),
            ("2026-09-01", "2026-09-30"),
            id="31-day month vs 30-day month clamps",
        ),
        pytest.param(
            ("2028-03-01", "2028-03-31"),
            "2028-04-02",
            ("2028-03-01", "2028-03-31"),
            ("2028-02-01", "2028-02-29"),
            id="march vs leap february",
        ),
        pytest.param(
            ("2027-03-01", "2027-03-31"),
            "2027-04-02",
            ("2027-03-01", "2027-03-31"),
            ("2027-02-01", "2027-02-28"),
            id="march vs common february",
        ),
        pytest.param(
            ("2027-01-01", "2027-01-31"),
            "2027-01-09",
            ("2027-01-01", "2027-01-09"),
            ("2026-12-01", "2026-12-09"),
            id="january vs previous year's december",
        ),
        pytest.param(
            ("2026-08-01", "2026-08-01"),
            "2026-08-20",
            ("2026-08-01", "2026-08-01"),
            ("2026-07-01", "2026-07-01"),
            id="single first-of-month day",
        ),
        pytest.param(
            ("2026-08-10", "2026-08-16"),
            "2026-08-20",
            ("2026-08-10", "2026-08-16"),
            ("2026-08-03", "2026-08-09"),
            id="arbitrary week vs week before",
        ),
        pytest.param(
            ("2026-08-15", "2026-08-15"),
            "2026-08-20",
            ("2026-08-15", "2026-08-15"),
            ("2026-08-14", "2026-08-14"),
            id="single mid-month day vs day before",
        ),
        pytest.param(
            ("2026-08-10", "2026-08-31"),
            "2026-08-12",
            ("2026-08-10", "2026-08-12"),
            ("2026-08-07", "2026-08-09"),
            id="arbitrary range clipped to today",
        ),
        pytest.param(
            ("2026-01-01", "2026-03-15"),
            "2026-12-01",
            ("2026-01-01", "2026-03-15"),
            ("2025-10-19", "2025-12-31"),
            id="multi-month window uses equal length, no overlap",
        ),
        pytest.param(
            ("2026-08-01", "2026-08-31"),
            "2026-08-01",
            ("2026-08-01", "2026-08-01"),
            ("2026-07-01", "2026-07-01"),
            id="period starting today",
        ),
    ],
)
def test_comparison_windows(
    period: tuple[str, str], today: str, current: tuple[str, str], previous: tuple[str, str]
) -> None:
    windows = comparison_windows(_period(*period), today=date.fromisoformat(today))

    assert windows is not None
    assert windows.current == _period(*current)
    assert windows.previous == _period(*previous)
    assert windows.previous.date_to < windows.current.date_from


def test_no_comparison_for_a_period_entirely_in_the_future() -> None:
    assert comparison_windows(_period("2026-09-01", "2026-09-30"), today=date(2026, 8, 31)) is None


@pytest.mark.parametrize(
    "period",
    [("2000-01-01", "2000-01-31"), ("2000-01-05", "2000-01-10")],
)
def test_no_comparison_before_the_earliest_supported_year(period: tuple[str, str]) -> None:
    assert comparison_windows(_period(*period), today=date(2026, 8, 31)) is None
