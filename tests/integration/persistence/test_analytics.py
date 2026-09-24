from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from mintflow.application.analytics import (
    CategoryTotal,
    CurrencyTotal,
    DailyTotal,
    DashboardPeriod,
    MerchantTotal,
)
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
    SqlAlchemyExpenseRepository,
)
from mintflow.infrastructure.persistence.models import ExpenseRecord, UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
AUGUST = DashboardPeriod(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
USD = CurrencyCode("USD")
EUR = CurrencyCode("EUR")


def _add_user(session: Session) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    return user.id


class Seeder:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.drafts = SqlAlchemyCaptureDraftRepository(session)
        self.expenses = SqlAlchemyExpenseRepository(session)

    def add(
        self,
        owner_id: UUID,
        *,
        amount: int = 1000,
        currency: CurrencyCode = USD,
        day: date = date(2026, 8, 10),
        category_key: str = "groceries",
        merchant: str | None = None,
        created_at: datetime = NOW,
    ) -> Expense:
        draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
        self.drafts.create(draft)
        expense = Expense.create(
            owner_id=owner_id,
            money=Money(minor_units=amount, currency=currency),
            transaction_date=TransactionDate(day),
            category_key=category_key,
            capture_draft_id=draft.id,
            merchant=MerchantName(merchant) if merchant is not None else None,
            source=CaptureSource.WEB_MANUAL,
            now=created_at,
        )
        self.expenses.create(expense)
        return expense

    def delete(self, expense: Expense) -> None:
        self.expenses.update(expense.delete(now=NOW))


@pytest.fixture
def seeder(db_session: Session) -> Seeder:
    return Seeder(db_session)


@pytest.fixture
def analytics(db_session: Session) -> SqlAlchemyAnalyticsRepository:
    return SqlAlchemyAnalyticsRepository(db_session)


def _add_noise(seeder: Seeder, owner_id: UUID, stranger_id: UUID) -> None:
    """Rows that must never contribute: another owner, deleted, and outside the period."""
    seeder.add(stranger_id, amount=99_999, merchant="Corner Shop")
    seeder.delete(seeder.add(owner_id, amount=88_888, merchant="Corner Shop"))
    seeder.add(owner_id, amount=77_777, day=date(2026, 7, 31), merchant="Corner Shop")
    seeder.add(owner_id, amount=66_666, day=date(2026, 9, 1), merchant="Corner Shop")


def test_currency_totals_are_separate_ranked_and_exclude_noise(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id, stranger_id = _add_user(db_session), _add_user(db_session)
    seeder.add(owner_id, amount=1000, currency=USD, day=date(2026, 8, 1))
    seeder.add(owner_id, amount=2500, currency=USD, day=date(2026, 8, 31))
    seeder.add(owner_id, amount=5000, currency=EUR)
    seeder.add(owner_id, amount=500, currency=CurrencyCode("GBP"))
    _add_noise(seeder, owner_id, stranger_id)

    totals = analytics.currency_totals(owner_id=owner_id, period=AUGUST)

    assert totals == (
        CurrencyTotal(currency=EUR, total_minor_units=5000, count=1),
        CurrencyTotal(currency=USD, total_minor_units=3500, count=2),
        CurrencyTotal(currency=CurrencyCode("GBP"), total_minor_units=500, count=1),
    )


def test_currency_ties_order_by_code(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id = _add_user(db_session)
    seeder.add(owner_id, amount=1000, currency=USD)
    seeder.add(owner_id, amount=1000, currency=EUR)

    totals = analytics.currency_totals(owner_id=owner_id, period=AUGUST)

    assert [total.currency for total in totals] == [EUR, USD]


def test_daily_totals_group_by_transaction_date_in_one_currency(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id, stranger_id = _add_user(db_session), _add_user(db_session)
    seeder.add(owner_id, amount=1000, day=date(2026, 8, 1))
    seeder.add(owner_id, amount=250, day=date(2026, 8, 1))
    seeder.add(owner_id, amount=4000, day=date(2026, 8, 31))
    seeder.add(owner_id, amount=9000, currency=EUR, day=date(2026, 8, 1))
    _add_noise(seeder, owner_id, stranger_id)

    totals = analytics.daily_totals(owner_id=owner_id, period=AUGUST, currency=USD)

    # The noise adds a deleted row on 10 Aug; only sparse days with active rows appear.
    assert totals == (
        DailyTotal(
            currency=USD, transaction_date=date(2026, 8, 1), total_minor_units=1250, count=2
        ),
        DailyTotal(
            currency=USD, transaction_date=date(2026, 8, 31), total_minor_units=4000, count=1
        ),
    )


def test_category_totals_carry_display_names_and_rank_with_key_ties(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id, stranger_id = _add_user(db_session), _add_user(db_session)
    seeder.add(owner_id, amount=3000, category_key="uncategorized")
    seeder.add(owner_id, amount=1000, category_key="transport")
    seeder.add(owner_id, amount=1000, category_key="health")
    seeder.add(owner_id, amount=500, category_key="health")
    seeder.add(owner_id, amount=7000, category_key="travel", currency=EUR)
    _add_noise(seeder, owner_id, stranger_id)

    totals = analytics.category_totals(owner_id=owner_id, period=AUGUST, currency=USD)

    assert [(total.category_key, total.total_minor_units, total.count) for total in totals] == [
        ("uncategorized", 3000, 1),
        ("health", 1500, 2),
        ("transport", 1000, 1),
    ]
    assert all(isinstance(total, CategoryTotal) and total.category_name for total in totals)
    assert totals[0].category_name == "Uncategorized"


def test_merchant_totals_use_exact_names_and_group_missing_merchants(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id, stranger_id = _add_user(db_session), _add_user(db_session)
    seeder.add(owner_id, amount=2000, merchant="Corner Shop")
    seeder.add(owner_id, amount=500, merchant="  Corner   Shop ")  # normalizes to the same name
    seeder.add(owner_id, amount=700, merchant="corner shop")  # differs only in case
    seeder.add(owner_id, amount=1000)
    seeder.add(owner_id, amount=1200)
    seeder.add(owner_id, amount=900, merchant="Bakery", currency=EUR)
    _add_noise(seeder, owner_id, stranger_id)

    totals = analytics.merchant_totals(owner_id=owner_id, period=AUGUST, currency=USD)

    assert totals == (
        MerchantTotal(currency=USD, merchant="Corner Shop", total_minor_units=2500, count=2),
        MerchantTotal(currency=USD, merchant=None, total_minor_units=2200, count=2),
        MerchantTotal(currency=USD, merchant="corner shop", total_minor_units=700, count=1),
    )


def test_largest_expense_is_deterministic_on_ties(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id, stranger_id = _add_user(db_session), _add_user(db_session)
    seeder.add(owner_id, amount=5000, day=date(2026, 8, 5))
    later_date = seeder.add(owner_id, amount=5000, day=date(2026, 8, 6))
    seeder.add(owner_id, amount=4999, day=date(2026, 8, 30))
    seeder.add(owner_id, amount=50_000, currency=EUR)
    _add_noise(seeder, owner_id, stranger_id)

    assert analytics.largest_expense(owner_id=owner_id, period=AUGUST, currency=USD) == later_date

    later_created = seeder.add(
        owner_id, amount=5000, day=date(2026, 8, 6), created_at=NOW + timedelta(minutes=1)
    )
    assert (
        analytics.largest_expense(owner_id=owner_id, period=AUGUST, currency=USD) == later_created
    )

    twins = [
        seeder.add(
            owner_id, amount=5000, day=date(2026, 8, 6), created_at=NOW + timedelta(minutes=2)
        )
        for _ in range(2)
    ]
    assert analytics.largest_expense(owner_id=owner_id, period=AUGUST, currency=USD) == max(
        twins, key=lambda expense: expense.id
    )


def test_all_currency_reads_also_group_by_currency(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository
) -> None:
    owner_id = _add_user(seeder.session)
    stranger_id = _add_user(seeder.session)
    _add_noise(seeder, owner_id, stranger_id)
    seeder.add(owner_id, amount=1000, currency=USD, merchant="Corner Shop")
    seeder.add(owner_id, amount=500, currency=USD, merchant="Corner Shop")
    seeder.add(owner_id, amount=700, currency=EUR, merchant="Corner Shop")
    seeder.add(owner_id, amount=300, currency=EUR, day=date(2026, 8, 11), category_key="health")

    daily = analytics.daily_totals(owner_id=owner_id, period=AUGUST, currency=None)
    categories = analytics.category_totals(owner_id=owner_id, period=AUGUST, currency=None)
    merchants = analytics.merchant_totals(owner_id=owner_id, period=AUGUST, currency=None)

    assert daily == (
        DailyTotal(
            currency=EUR, transaction_date=date(2026, 8, 10), total_minor_units=700, count=1
        ),
        DailyTotal(
            currency=USD, transaction_date=date(2026, 8, 10), total_minor_units=1500, count=2
        ),
        DailyTotal(
            currency=EUR, transaction_date=date(2026, 8, 11), total_minor_units=300, count=1
        ),
    )
    assert [
        (total.currency, total.category_key, total.total_minor_units, total.count)
        for total in categories
    ] == [
        (USD, "groceries", 1500, 2),
        (EUR, "groceries", 700, 1),
        (EUR, "health", 300, 1),
    ]
    assert merchants == (
        MerchantTotal(currency=USD, merchant="Corner Shop", total_minor_units=1500, count=2),
        MerchantTotal(currency=EUR, merchant="Corner Shop", total_minor_units=700, count=1),
        MerchantTotal(currency=EUR, merchant=None, total_minor_units=300, count=1),
    )


def test_empty_period_returns_empty_results(
    analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id = _add_user(db_session)

    assert analytics.currency_totals(owner_id=owner_id, period=AUGUST) == ()
    assert analytics.daily_totals(owner_id=owner_id, period=AUGUST, currency=USD) == ()
    assert analytics.category_totals(owner_id=owner_id, period=AUGUST, currency=USD) == ()
    assert analytics.merchant_totals(owner_id=owner_id, period=AUGUST, currency=USD) == ()
    assert analytics.largest_expense(owner_id=owner_id, period=AUGUST, currency=USD) is None


def test_sums_beyond_64_bits_stay_exact(
    seeder: Seeder, analytics: SqlAlchemyAnalyticsRepository, db_session: Session
) -> None:
    owner_id = _add_user(db_session)
    near_bigint_max = 9_000_000_000_000_000_000
    for _ in range(2):
        expense = seeder.add(owner_id, merchant="Big")
        # Bypasses the domain's own amount ceiling to exercise the database sum.
        record = db_session.get(ExpenseRecord, expense.id)
        assert record is not None
        record.amount_minor_units = near_bigint_max
    db_session.commit()

    [total] = analytics.currency_totals(owner_id=owner_id, period=AUGUST)
    [daily] = analytics.daily_totals(owner_id=owner_id, period=AUGUST, currency=USD)

    assert total.total_minor_units == daily.total_minor_units == 2 * near_bigint_max
    assert isinstance(total.total_minor_units, int)
