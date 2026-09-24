"""Aggregates the dashboard reads (docs/dashboard_design.md, D9).

Every value is computed in the database over confirmed, non-deleted
Expenses of one owner. Totals are exact integers in minor units and never
mix currencies: every row carries its currency, and a ``currency`` of None
groups by currency as well (docs/exchange_rates_design.md, X4).
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
    currency: CurrencyCode
    transaction_date: date
    total_minor_units: int
    count: int


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    currency: CurrencyCode
    category_key: str
    category_name: str
    total_minor_units: int
    count: int


@dataclass(frozen=True, slots=True)
class MerchantTotal:
    """``merchant`` is None for the group of Expenses without a merchant in a currency."""

    currency: CurrencyCode
    merchant: str | None
    total_minor_units: int
    count: int


class AnalyticsRepository(Protocol):
    def currency_totals(
        self, *, owner_id: UUID, period: DashboardPeriod
    ) -> tuple[CurrencyTotal, ...]: ...

    def daily_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None
    ) -> tuple[DailyTotal, ...]: ...

    def category_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None
    ) -> tuple[CategoryTotal, ...]: ...

    def merchant_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None
    ) -> tuple[MerchantTotal, ...]: ...

    def largest_expense(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> Expense | None: ...
