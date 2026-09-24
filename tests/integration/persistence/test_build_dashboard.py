from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from mintflow.application.analytics import (
    BucketGranularity,
    BuildDashboard,
    ConvertTo,
    CurrencyTotal,
    DashboardPeriod,
)
from mintflow.application.rates import ExchangeRate
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyAnalyticsRepository,
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExchangeRateRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyUserRepository,
)
from mintflow.infrastructure.persistence.models import UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
USD = CurrencyCode("USD")
EUR = CurrencyCode("EUR")
GBP = CurrencyCode("GBP")
BHD = CurrencyCode("BHD")


def _add_user(session: Session, *, default_currency: str | None = None) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    if default_currency is not None:
        session.execute(
            update(UserRecord)
            .where(UserRecord.id == user.id)
            .values(default_currency=default_currency)
        )
        session.commit()
    return user.id


def _add(
    session: Session,
    owner_id: UUID,
    amount: int,
    day: int,
    *,
    currency: CurrencyCode = EUR,
    month: int = 8,
    category_key: str = "groceries",
    merchant: str | None = None,
) -> Expense:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    expense = Expense.create(
        owner_id=owner_id,
        money=Money(minor_units=amount, currency=currency),
        transaction_date=TransactionDate(date(2026, month, day)),
        category_key=category_key,
        capture_draft_id=draft.id,
        merchant=MerchantName(merchant) if merchant is not None else None,
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )
    SqlAlchemyExpenseRepository(session).create(expense)
    return expense


def _use_case(session: Session) -> BuildDashboard:
    return BuildDashboard(
        analytics=SqlAlchemyAnalyticsRepository(session),
        user_repository=SqlAlchemyUserRepository(session),
        clock=lambda: NOW,
    )


def test_realistic_multi_currency_month_end_to_end(db_session: Session) -> None:
    owner_id = _add_user(db_session, default_currency="EUR")
    stranger_id = _add_user(db_session)

    _add(db_session, owner_id, 4_250, 2, merchant="Supermarket")
    _add(db_session, owner_id, 1_990, 9, merchant="Supermarket")
    _add(db_session, owner_id, 3_600, 5, category_key="transport", merchant="Rail")
    _add(db_session, owner_id, 12_000, 14, category_key="housing", merchant="Landlord")
    _add(db_session, owner_id, 850, 15, category_key="food_and_dining", merchant="Cafe")
    _add(db_session, owner_id, 700, 16, category_key="food_and_dining", merchant="Bakery")
    _add(db_session, owner_id, 450, 17, category_key="food_and_dining", merchant="Kiosk")
    _add(db_session, owner_id, 2_000, 18, category_key="uncategorized")
    _add(db_session, owner_id, 9_999, 3, currency=USD, category_key="travel", merchant="Airline")
    _add(db_session, owner_id, 5_000, 10, month=7, merchant="Supermarket")  # previous period
    _add(db_session, owner_id, 8_000, 25, month=7)  # after the compared July window
    deleted = _add(db_session, owner_id, 50_000, 11, category_key="shopping", merchant="Mall")
    SqlAlchemyExpenseRepository(db_session).update(deleted.delete(now=NOW))
    _add(db_session, stranger_id, 77_777, 12, merchant="Supermarket")

    dashboard = _use_case(db_session).execute(caller_id=owner_id)

    assert dashboard.period == DashboardPeriod(
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 31)
    )
    assert [(t.currency, t.total_minor_units, t.count) for t in dashboard.currencies] == [
        (EUR, 25_840, 8),
        (USD, 9_999, 1),
    ]
    assert dashboard.currency == EUR  # the default currency, present among two
    assert dashboard.currency_selection_required is False
    detail = dashboard.detail
    assert detail is not None

    assert (detail.summary.total_minor_units, detail.summary.count) == (25_840, 8)
    comparison = detail.summary.comparison
    assert comparison is not None
    assert comparison.previous_period == DashboardPeriod(
        date_from=date(2026, 7, 1), date_to=date(2026, 7, 20)
    )
    assert comparison.previous_total_minor_units == 5_000
    assert comparison.change_minor_units == 20_840
    assert comparison.change_basis_points == 41_680

    over_time = detail.spending_over_time
    assert over_time.granularity is BucketGranularity.DAY
    assert len(over_time.buckets) == 31
    assert sum(bucket.total_minor_units for bucket in over_time.buckets) == 25_840

    # food_and_dining and uncategorized tie at 2 000; the key breaks the tie.
    assert [(c.category_key, c.total_minor_units) for c in detail.categories] == [
        ("housing", 12_000),
        ("groceries", 6_240),
        ("transport", 3_600),
        ("food_and_dining", 2_000),
        ("uncategorized", 2_000),
    ]
    assert detail.categories[0].category_name == "Housing"

    top = detail.top_merchants
    assert [(m.merchant, m.total_minor_units) for m in top.merchants] == [
        ("Landlord", 12_000),
        ("Supermarket", 6_240),
        ("Rail", 3_600),
        ("Cafe", 850),
        ("Bakery", 700),
    ]
    assert top.other is not None
    assert (top.other.total_minor_units, top.other.count) == (2_450, 2)

    assert detail.insights.largest_category is not None
    assert detail.insights.largest_category.category_key == "housing"
    assert detail.insights.largest_category.share_basis_points == 4644
    largest = detail.insights.largest_expense
    assert largest is not None
    assert (largest.money, largest.merchant, largest.transaction_date) == (
        Money(minor_units=12_000, currency=EUR),
        "Landlord",
        date(2026, 8, 14),
    )


def test_multi_currency_month_without_default_requires_a_choice(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    _add(db_session, owner_id, 1_000, 2)
    _add(db_session, owner_id, 2_000, 3, currency=USD)

    use_case = _use_case(db_session)
    unselected = use_case.execute(caller_id=owner_id)
    selected = use_case.execute(caller_id=owner_id, currency=USD)

    assert unselected.currency_selection_required is True
    assert unselected.detail is None
    assert selected.detail is not None
    assert selected.detail.summary.total_minor_units == 2_000


def test_converted_month_with_several_currencies_and_one_without_a_rate(
    db_session: Session,
) -> None:
    owner_id = _add_user(db_session, default_currency="EUR")
    stranger_id = _add_user(db_session)
    rates = SqlAlchemyExchangeRateRepository(db_session)
    rates.save(
        [
            ExchangeRate(USD, Decimal("1.25"), date(2026, 8, 19), "ECB"),
            ExchangeRate(GBP, Decimal("0.8"), date(2026, 8, 19), "ECB"),
        ],
        fetched_at=NOW,
    )

    _add(db_session, owner_id, 1_000, 2, merchant="Market")
    _add(db_session, owner_id, 3_000, 3, currency=USD, merchant="Market")  # 24.00 EUR
    _add(db_session, owner_id, 500, 3, currency=GBP, category_key="health")  # 6.25 EUR
    _add(db_session, owner_id, 10_000, 4, currency=BHD, merchant="Market")  # no rate
    _add(db_session, owner_id, 800, 10, currency=USD, month=7)  # 6.40 EUR, previous window
    _add(db_session, stranger_id, 77_777, 3, currency=USD, merchant="Market")

    dashboard = _use_case(db_session).execute(
        caller_id=owner_id, convert_to=ConvertTo(EUR, rates.latest())
    )

    assert dashboard.currency == EUR
    assert dashboard.conversion is not None
    assert [rate.currency for rate in dashboard.conversion.rates] == [GBP, USD]
    assert dashboard.conversion.unconverted == (
        CurrencyTotal(currency=BHD, total_minor_units=10_000, count=1),
    )
    detail = dashboard.detail
    assert detail is not None
    assert (detail.summary.total_minor_units, detail.summary.count) == (4_025, 3)
    comparison = detail.summary.comparison
    assert comparison is not None
    assert (comparison.previous_total_minor_units, comparison.current_total_minor_units) == (
        640,
        4_025,
    )
    assert comparison.change_basis_points == 52_891

    by_day = {bucket.period.date_from: bucket for bucket in detail.spending_over_time.buckets}
    assert by_day[date(2026, 8, 3)].total_minor_units == 3_025
    assert by_day[date(2026, 8, 4)].total_minor_units == 0
    assert sum(bucket.total_minor_units for bucket in by_day.values()) == 4_025

    assert [(c.category_key, c.total_minor_units, c.count) for c in detail.categories] == [
        ("groceries", 3_400, 2),
        ("health", 625, 1),
    ]
    assert [(m.merchant, m.total_minor_units, m.count) for m in detail.top_merchants.merchants] == [
        ("Market", 3_400, 2)
    ]
    assert detail.top_merchants.other is not None
    assert detail.top_merchants.other.total_minor_units == 625
    largest = detail.insights.largest_expense
    assert largest is not None
    assert (largest.money, largest.converted_minor_units) == (
        Money(minor_units=3_000, currency=USD),
        2_400,
    )
