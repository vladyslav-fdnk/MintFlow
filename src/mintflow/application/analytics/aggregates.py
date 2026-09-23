"""Aggregates the dashboard reads (docs/dashboard_design.md, D9).

Every value is computed in the database over confirmed, non-deleted
Expenses of one owner. Totals are exact integers in minor units and never
mix currencies.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from mintflow.application.analytics.periods import DashboardPeriod
from mintflow.domain.capture import CurrencyCode, Expense


@dataclass(frozen=True, slots=True)
class CurrencyTotal:
    currency: CurrencyCode
    total_minor_units: int
    count: int


@dataclass(frozen=True, slots=True)
class DailyTotal:
    transaction_date: date
    total_minor_units: int
    count: int


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category_key: str
    category_name: str
    total_minor_units: int
    count: int


@dataclass(frozen=True, slots=True)
class MerchantTotal:
    """``merchant`` is None for the one group of Expenses without a merchant."""

    merchant: str | None
    total_minor_units: int
    count: int


class AnalyticsRepository(Protocol):
    def currency_totals(
        self, *, owner_id: UUID, period: DashboardPeriod
    ) -> tuple[CurrencyTotal, ...]: ...

    def daily_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> tuple[DailyTotal, ...]: ...

    def category_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> tuple[CategoryTotal, ...]: ...

    def merchant_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> tuple[MerchantTotal, ...]: ...

    def largest_expense(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> Expense | None: ...
