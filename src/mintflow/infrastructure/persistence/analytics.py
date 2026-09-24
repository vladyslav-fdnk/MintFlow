from uuid import UUID

from sqlalchemy import Numeric, Select, cast, func
from sqlalchemy.orm import Session

from mintflow.application.analytics.aggregates import (
    CategoryTotal,
    CurrencyTotal,
    DailyTotal,
    MerchantTotal,
)
from mintflow.application.analytics.periods import DashboardPeriod
from mintflow.domain.capture import CurrencyCode, Expense
from mintflow.infrastructure.persistence.expenses import (
    expense_from_record,
    select_active_expenses,
)
from mintflow.infrastructure.persistence.models import CategoryRecord, ExpenseRecord

# SUM(bigint) is numeric in PostgreSQL, so large totals never overflow or
# round; the exact value is converted to int in Python.
_TOTAL = cast(func.sum(ExpenseRecord.amount_minor_units), Numeric)
_COUNT = func.count(ExpenseRecord.id)


def _active_in(
    owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None = None
) -> Select[tuple[ExpenseRecord]]:
    statement = select_active_expenses(owner_id).where(
        ExpenseRecord.transaction_date >= period.date_from,
        ExpenseRecord.transaction_date <= period.date_to,
    )
    if currency is not None:
        statement = statement.where(ExpenseRecord.amount_currency == currency.value)
    return statement


class SqlAlchemyAnalyticsRepository:
    """Read-only dashboard aggregates over one owner's active Expenses (design D9).

    A ``currency`` of None reads every currency, still grouped by currency.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def currency_totals(
        self, *, owner_id: UUID, period: DashboardPeriod
    ) -> tuple[CurrencyTotal, ...]:
        statement = (
            _active_in(owner_id, period)
            .with_only_columns(ExpenseRecord.amount_currency, _TOTAL, _COUNT)
            .group_by(ExpenseRecord.amount_currency)
            .order_by(_TOTAL.desc(), ExpenseRecord.amount_currency)
        )
        return tuple(
            CurrencyTotal(
                currency=CurrencyCode(currency), total_minor_units=int(total), count=count
            )
            for currency, total, count in self._session.execute(statement)
        )

    def daily_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None
    ) -> tuple[DailyTotal, ...]:
        statement = (
            _active_in(owner_id, period, currency)
            .with_only_columns(
                ExpenseRecord.amount_currency, ExpenseRecord.transaction_date, _TOTAL, _COUNT
            )
            .group_by(ExpenseRecord.amount_currency, ExpenseRecord.transaction_date)
            .order_by(ExpenseRecord.transaction_date, ExpenseRecord.amount_currency)
        )
        return tuple(
            DailyTotal(
                currency=CurrencyCode(code),
                transaction_date=day,
                total_minor_units=int(total),
                count=count,
            )
            for code, day, total, count in self._session.execute(statement)
        )

    def category_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None
    ) -> tuple[CategoryTotal, ...]:
        statement = (
            _active_in(owner_id, period, currency)
            .with_only_columns(
                ExpenseRecord.amount_currency,
                ExpenseRecord.category_key,
                CategoryRecord.name,
                _TOTAL,
                _COUNT,
            )
            .join(CategoryRecord, CategoryRecord.key == ExpenseRecord.category_key)
            .group_by(
                ExpenseRecord.amount_currency, ExpenseRecord.category_key, CategoryRecord.name
            )
            .order_by(_TOTAL.desc(), ExpenseRecord.category_key, ExpenseRecord.amount_currency)
        )
        return tuple(
            CategoryTotal(
                currency=CurrencyCode(code),
                category_key=key,
                category_name=name,
                total_minor_units=int(total),
                count=count,
            )
            for code, key, name, total, count in self._session.execute(statement)
        )

    def merchant_totals(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode | None
    ) -> tuple[MerchantTotal, ...]:
        statement = (
            _active_in(owner_id, period, currency)
            .with_only_columns(
                ExpenseRecord.amount_currency, ExpenseRecord.merchant_name, _TOTAL, _COUNT
            )
            .group_by(ExpenseRecord.amount_currency, ExpenseRecord.merchant_name)
            .order_by(
                _TOTAL.desc(),
                ExpenseRecord.merchant_name.asc().nulls_last(),
                ExpenseRecord.amount_currency,
            )
        )
        return tuple(
            MerchantTotal(
                currency=CurrencyCode(code),
                merchant=merchant,
                total_minor_units=int(total),
                count=count,
            )
            for code, merchant, total, count in self._session.execute(statement)
        )

    def largest_expense(
        self, *, owner_id: UUID, period: DashboardPeriod, currency: CurrencyCode
    ) -> Expense | None:
        """Greatest amount; ties go to the later date, then later creation, then id."""
        record = self._session.scalar(
            _active_in(owner_id, period, currency)
            .order_by(
                ExpenseRecord.amount_minor_units.desc(),
                ExpenseRecord.transaction_date.desc(),
                ExpenseRecord.created_at.desc(),
                ExpenseRecord.id.desc(),
            )
            .limit(1)
        )
        return expense_from_record(record) if record is not None else None
