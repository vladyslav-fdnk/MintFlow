"""Dashboard period arithmetic (docs/dashboard_design.md, D2, D4, D5).

Pure functions over calendar dates. Expenses are grouped by their stored
transaction date; the user's timezone is used only to find "today".
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from mintflow.domain.user import Timezone

MAX_PERIOD_DAYS: Final = 366
MAX_DAILY_BUCKET_PERIOD_DAYS: Final = 31
_MIN_YEAR: Final = 2000
_MAX_YEAR: Final = 2100


@dataclass(frozen=True, slots=True)
class DashboardPeriod:
    """An inclusive range of calendar dates the dashboard summarizes."""

    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        if self.days > MAX_PERIOD_DAYS:
            raise ValueError(f"a period may span at most {MAX_PERIOD_DAYS} days")
        if self.date_from.year < _MIN_YEAR or self.date_to.year > _MAX_YEAR:
            raise ValueError(f"period years must be between {_MIN_YEAR} and {_MAX_YEAR}")

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1


class BucketGranularity(StrEnum):
    DAY = "day"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class ChartBuckets:
    granularity: BucketGranularity
    buckets: tuple[DashboardPeriod, ...]


@dataclass(frozen=True, slots=True)
class ComparisonWindows:
    """The selected period clipped to today, and the window it is compared with."""

    current: DashboardPeriod
    previous: DashboardPeriod


def local_today(*, now: datetime, timezone: Timezone) -> date:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(timezone.value)).date()


def _last_day_of_month(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _first_day_of_previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def default_period(*, now: datetime, timezone: Timezone) -> DashboardPeriod:
    """The current calendar month in the user's timezone, first to last day."""
    today = local_today(now=now, timezone=timezone)
    return DashboardPeriod(date_from=today.replace(day=1), date_to=_last_day_of_month(today))


def chart_buckets(period: DashboardPeriod) -> ChartBuckets:
    """Daily buckets up to 31 days, else calendar months clipped to the period."""
    if period.days <= MAX_DAILY_BUCKET_PERIOD_DAYS:
        days = tuple(
            DashboardPeriod(date_from=day, date_to=day)
            for day in (period.date_from + timedelta(days=offset) for offset in range(period.days))
        )
        return ChartBuckets(granularity=BucketGranularity.DAY, buckets=days)

    months: list[DashboardPeriod] = []
    start = period.date_from
    while start <= period.date_to:
        end = min(_last_day_of_month(start), period.date_to)
        months.append(DashboardPeriod(date_from=start, date_to=end))
        start = end + timedelta(days=1)
    return ChartBuckets(granularity=BucketGranularity.MONTH, buckets=tuple(months))


def comparison_windows(period: DashboardPeriod, *, today: date) -> ComparisonWindows | None:
    """The previous comparable period for the part of ``period`` up to ``today``.

    A window that starts on the first of a month and stays inside that month
    is compared with the same day offsets one month earlier, clamped to that
    month's length (1-23 Aug vs 1-23 Jul). Any other window is compared with
    the equal-length window immediately before it. Returns None when the
    period starts after today, or when the previous window would fall before
    the earliest supported year, where no Expense can exist.
    """
    if period.date_from > today:
        return None
    current = DashboardPeriod(date_from=period.date_from, date_to=min(period.date_to, today))

    stays_in_one_month = (current.date_from.year, current.date_from.month) == (
        current.date_to.year,
        current.date_to.month,
    )
    if current.date_from.day == 1 and stays_in_one_month:
        previous_from = _first_day_of_previous_month(current.date_from)
        previous_to = min(
            previous_from + timedelta(days=current.days - 1), _last_day_of_month(previous_from)
        )
    else:
        previous_to = current.date_from - timedelta(days=1)
        previous_from = previous_to - timedelta(days=current.days - 1)

    if previous_from.year < _MIN_YEAR:
        return None
    return ComparisonWindows(
        current=current,
        previous=DashboardPeriod(date_from=previous_from, date_to=previous_to),
    )
