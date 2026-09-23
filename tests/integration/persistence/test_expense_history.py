from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mintflow.application.capture import (
    ExpenseHistoryFilter,
    ExpenseHistoryPosition,
    decode_history_cursor,
    encode_history_cursor,
)
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
)
from mintflow.infrastructure.persistence.models import UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
NO_FILTER = ExpenseHistoryFilter()


def _add_user(session: Session) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    return user.id


def _add_expense(
    session: Session,
    owner_id: UUID,
    *,
    transaction_date: date = date(2026, 8, 4),
    created_at: datetime = NOW,
    category_key: str = "groceries",
    currency: str = "USD",
) -> Expense:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    expense = Expense.create(
        owner_id=owner_id,
        money=Money(minor_units=1500, currency=CurrencyCode(currency)),
        transaction_date=TransactionDate(transaction_date),
        category_key=category_key,
        capture_draft_id=draft.id,
        source=CaptureSource.WEB_MANUAL,
        now=created_at,
    )
    SqlAlchemyExpenseRepository(session).create(expense)
    return expense


def _sort_key(expense: Expense) -> tuple[date, datetime, UUID]:
    return (expense.transaction_date.value, expense.created_at, expense.id)


def _walk(
    repository: SqlAlchemyExpenseRepository,
    owner_id: UUID,
    *,
    limit: int,
    history_filter: ExpenseHistoryFilter = NO_FILTER,
) -> list[Expense]:
    """Follow cursors through their opaque string form, as a client would."""
    collected: list[Expense] = []
    after: ExpenseHistoryPosition | None = None
    while True:
        page = repository.list_history(
            owner_id=owner_id, history_filter=history_filter, limit=limit, after=after
        )
        assert len(page.items) <= limit
        collected.extend(page.items)
        if page.next_position is None:
            return collected
        after = decode_history_cursor(encode_history_cursor(page.next_position))


def _seed_with_ties(session: Session, owner_id: UUID) -> list[Expense]:
    expenses = []
    for day_offset in range(3):
        for minute in range(2):
            for _ in range(2):
                # Two Expenses share every (date, created_at) pair: only id breaks the tie.
                expenses.append(
                    _add_expense(
                        session,
                        owner_id,
                        transaction_date=date(2026, 8, 1) + timedelta(days=day_offset),
                        created_at=NOW + timedelta(minutes=minute),
                    )
                )
    return expenses


def test_history_is_ordered_newest_first_with_id_breaking_ties(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    expenses = _seed_with_ties(db_session, owner_id)
    repository = SqlAlchemyExpenseRepository(db_session)

    page = repository.list_history(owner_id=owner_id, history_filter=NO_FILTER, limit=100)

    assert list(page.items) == sorted(expenses, key=_sort_key, reverse=True)
    assert page.next_position is None


@pytest.mark.parametrize("limit", [1, 2, 3, 5, 11, 12, 100])
def test_paging_returns_every_expense_exactly_once_in_order(
    db_session: Session, limit: int
) -> None:
    owner_id = _add_user(db_session)
    expenses = _seed_with_ties(db_session, owner_id)
    repository = SqlAlchemyExpenseRepository(db_session)

    walked = _walk(repository, owner_id, limit=limit)

    assert walked == sorted(expenses, key=_sort_key, reverse=True)


def test_last_full_page_reports_no_next_position(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    for _ in range(2):
        _add_expense(db_session, owner_id)
    repository = SqlAlchemyExpenseRepository(db_session)

    page = repository.list_history(owner_id=owner_id, history_filter=NO_FILTER, limit=2)

    assert len(page.items) == 2
    assert page.next_position is None


def test_empty_history_returns_an_empty_page(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    repository = SqlAlchemyExpenseRepository(db_session)

    page = repository.list_history(owner_id=owner_id, history_filter=NO_FILTER, limit=50)

    assert page.items == ()
    assert page.next_position is None


def test_date_bounds_are_inclusive(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    by_day = {
        day: _add_expense(db_session, owner_id, transaction_date=date(2026, 8, day))
        for day in range(1, 6)
    }
    repository = SqlAlchemyExpenseRepository(db_session)

    walked = _walk(
        repository,
        owner_id,
        limit=2,
        history_filter=ExpenseHistoryFilter(date_from=date(2026, 8, 2), date_to=date(2026, 8, 4)),
    )
    only_from = _walk(
        repository,
        owner_id,
        limit=50,
        history_filter=ExpenseHistoryFilter(date_from=date(2026, 8, 4)),
    )
    only_to = _walk(
        repository,
        owner_id,
        limit=50,
        history_filter=ExpenseHistoryFilter(date_to=date(2026, 8, 2)),
    )

    assert walked == [by_day[4], by_day[3], by_day[2]]
    assert only_from == [by_day[5], by_day[4]]
    assert only_to == [by_day[2], by_day[1]]


def test_category_and_currency_filters_combine_with_and(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    groceries_usd = _add_expense(db_session, owner_id, category_key="groceries", currency="USD")
    groceries_eur = _add_expense(db_session, owner_id, category_key="groceries", currency="EUR")
    transport_usd = _add_expense(db_session, owner_id, category_key="transport", currency="USD")
    health_gbp = _add_expense(db_session, owner_id, category_key="health", currency="GBP")
    repository = SqlAlchemyExpenseRepository(db_session)

    def keys(history_filter: ExpenseHistoryFilter) -> set[UUID]:
        return {
            expense.id
            for expense in _walk(repository, owner_id, limit=1, history_filter=history_filter)
        }

    assert keys(ExpenseHistoryFilter(category_keys=frozenset({"groceries"}))) == {
        groceries_usd.id,
        groceries_eur.id,
    }
    assert keys(ExpenseHistoryFilter(category_keys=frozenset({"groceries", "health"}))) == {
        groceries_usd.id,
        groceries_eur.id,
        health_gbp.id,
    }
    assert keys(ExpenseHistoryFilter(currencies=frozenset({CurrencyCode("USD")}))) == {
        groceries_usd.id,
        transport_usd.id,
    }
    assert keys(
        ExpenseHistoryFilter(
            category_keys=frozenset({"groceries"}),
            currencies=frozenset({CurrencyCode("USD"), CurrencyCode("GBP")}),
            date_from=date(2026, 8, 4),
            date_to=date(2026, 8, 4),
        )
    ) == {groceries_usd.id}
    assert keys(ExpenseHistoryFilter(category_keys=frozenset({"not_a_category"}))) == set()


def test_history_is_owner_scoped_even_with_another_owners_cursor(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    other_id = _add_user(db_session)
    own = [_add_expense(db_session, owner_id) for _ in range(3)]
    others = [_add_expense(db_session, other_id) for _ in range(3)]
    repository = SqlAlchemyExpenseRepository(db_session)
    newest_other = max(others, key=_sort_key)
    # A position just above every one of the other owner's rows.
    foreign_position = ExpenseHistoryPosition(
        transaction_date=newest_other.transaction_date.value + timedelta(days=1),
        created_at=newest_other.created_at,
        expense_id=newest_other.id,
    )

    walked = _walk(repository, owner_id, limit=2)
    with_foreign_cursor = repository.list_history(
        owner_id=owner_id, history_filter=NO_FILTER, limit=100, after=foreign_position
    )

    assert {expense.id for expense in walked} == {expense.id for expense in own}
    assert {expense.owner_id for expense in with_foreign_cursor.items} == {owner_id}
    assert repository.get_active(expense_id=others[0].id, owner_id=owner_id) is None


def test_soft_deleted_expenses_are_excluded_from_history_and_active_reads(
    db_session: Session,
) -> None:
    owner_id = _add_user(db_session)
    kept = _add_expense(db_session, owner_id)
    deleted = _add_expense(db_session, owner_id)
    repository = SqlAlchemyExpenseRepository(db_session)
    repository.update(deleted.delete(now=NOW + timedelta(minutes=1)))

    walked = _walk(repository, owner_id, limit=1)

    assert walked == [kept]
    assert repository.get_active(expense_id=kept.id, owner_id=owner_id) == kept
    assert repository.get_active(expense_id=deleted.id, owner_id=owner_id) is None
    # The any-state read used by confirmation idempotency still finds it.
    assert repository.get(expense_id=deleted.id, owner_id=owner_id) is not None


def test_changes_between_pages_do_not_duplicate_or_skip_surviving_rows(
    db_session: Session,
) -> None:
    owner_id = _add_user(db_session)
    expenses = [
        _add_expense(db_session, owner_id, transaction_date=date(2026, 8, day))
        for day in range(1, 7)
    ]
    repository = SqlAlchemyExpenseRepository(db_session)

    first = repository.list_history(owner_id=owner_id, history_filter=NO_FILTER, limit=2)
    assert first.next_position is not None
    # Between pages: one row already shown is deleted, one not yet shown is
    # deleted, a new row lands on the already-read part of the order, and
    # another lands on the unread part.
    repository.update(first.items[0].delete(now=NOW + timedelta(minutes=1)))
    repository.update(expenses[1].delete(now=NOW + timedelta(minutes=1)))
    _add_expense(db_session, owner_id, transaction_date=date(2026, 8, 10))
    # Same date as an unread row but created later, so it sorts just before it.
    unread_insert = _add_expense(
        db_session,
        owner_id,
        transaction_date=date(2026, 8, 3),
        created_at=NOW + timedelta(minutes=1),
    )
    rest: list[Expense] = []
    after: ExpenseHistoryPosition | None = first.next_position
    while after is not None:
        page = repository.list_history(
            owner_id=owner_id, history_filter=NO_FILTER, limit=2, after=after
        )
        rest.extend(page.items)
        after = page.next_position

    seen = [expense.id for expense in [*first.items, *rest]]
    assert len(seen) == len(set(seen))
    surviving_unread = [expenses[3], unread_insert, expenses[2], expenses[0]]
    assert [expense.id for expense in rest] == [expense.id for expense in surviving_unread]


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_limit_outside_the_allowed_range_is_rejected(db_session: Session, limit: int) -> None:
    owner_id = _add_user(db_session)
    repository = SqlAlchemyExpenseRepository(db_session)

    with pytest.raises(ValueError, match="limit"):
        repository.list_history(owner_id=owner_id, history_filter=NO_FILTER, limit=limit)


def test_active_history_index_exists_and_can_serve_the_listing(db_session: Session) -> None:
    owner_id = _add_user(db_session)
    _add_expense(db_session, owner_id)
    definition = db_session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_expenses_active_history'")
    )
    assert definition is not None
    assert "(owner_id, transaction_date DESC, created_at DESC, id DESC)" in definition
    assert "WHERE (deleted_at IS NULL)" in definition

    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        db_session.scalars(
            text(
                "EXPLAIN SELECT id FROM expenses "
                "WHERE owner_id = :owner_id AND deleted_at IS NULL "
                "ORDER BY transaction_date DESC, created_at DESC, id DESC LIMIT 51"
            ),
            {"owner_id": owner_id},
        ).all()
    )

    assert "ix_expenses_active_history" in plan
