from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mintflow.application.capture import (
    EditExpense,
    ExpenseChangeRecord,
    ExpenseChangeType,
    ExpenseEdit,
    ExpenseEditRejected,
    ExpenseField,
    ExpenseFieldChange,
    ExpenseNotFound,
    is_within_future_tolerance,
)
from mintflow.domain.capture import (
    CaptureSource,
    Category,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.domain.user import Timezone, User

CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _user(timezone: str = "UTC") -> User:
    return User.create(now=CREATED, timezone=Timezone(timezone))


def _expense(owner_id: UUID) -> Expense:
    return Expense.create(
        owner_id=owner_id,
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 4)),
        category_key="groceries",
        capture_draft_id=uuid4(),
        merchant=MerchantName("Corner Shop"),
        note="milk",
        source=CaptureSource.WEB_MANUAL,
        now=CREATED,
    )


class FakeExpenseRepository:
    def __init__(self, expense: Expense | None) -> None:
        self.expense = expense
        self.updates: list[tuple[Expense, bool]] = []

    def get_active_for_update(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        expense = self.expense
        if expense is None or expense.id != expense_id or expense.owner_id != owner_id:
            return None
        return expense if expense.is_active else None

    def update(self, expense: Expense, *, commit: bool = True) -> None:
        self.updates.append((expense, commit))


class FakeChangeRecords:
    def __init__(self) -> None:
        self.records: list[ExpenseChangeRecord] = []

    def append(self, record: ExpenseChangeRecord) -> None:
        self.records.append(record)


class FakeCategories:
    categories = {
        "groceries": Category(id=uuid4(), key="groceries", name="Groceries", is_active=True),
        "health": Category(id=uuid4(), key="health", name="Health", is_active=True),
        "retired": Category(id=uuid4(), key="retired", name="Retired", is_active=False),
    }

    def get_by_key(self, key: str) -> Category | None:
        return self.categories.get(key)


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user

    def get(self, user_id: UUID) -> User | None:
        return self.user if self.user is not None and self.user.id == user_id else None


class Harness:
    def __init__(self, *, timezone: str = "UTC", now: datetime = NOW) -> None:
        self.user = _user(timezone)
        self.expense = _expense(self.user.id)
        self.expenses = FakeExpenseRepository(self.expense)
        self.records = FakeChangeRecords()
        self.use_case = EditExpense(
            expense_repository=self.expenses,
            change_records=self.records,
            category_repository=FakeCategories(),
            user_repository=FakeUsers(self.user),
            clock=lambda: now,
        )

    def edit(self, edit: ExpenseEdit, *, caller_id: UUID | None = None) -> Expense:
        return self.use_case.execute(
            expense_id=self.expense.id,
            caller_id=self.user.id if caller_id is None else caller_id,
            edit=edit,
        )

    def assert_nothing_written(self) -> None:
        assert self.records.records == []
        assert self.expenses.updates == []


def test_edits_every_field_and_records_each_change() -> None:
    harness = Harness()

    edited = harness.edit(
        ExpenseEdit(
            money=Money(minor_units=2000, currency=CurrencyCode("EUR")),
            transaction_date=TransactionDate(date(2026, 8, 3)),
            merchant=MerchantName("Bakery"),
            category_key="health",
            note="bread",
        )
    )

    assert edited.money == Money(minor_units=2000, currency=CurrencyCode("EUR"))
    assert edited.transaction_date == TransactionDate(date(2026, 8, 3))
    assert edited.merchant == MerchantName("Bakery")
    assert edited.category_key == "health"
    assert edited.note == "bread"
    assert edited.modified_at == NOW
    assert edited.created_at == CREATED
    assert harness.expenses.updates == [(edited, True)]
    [record] = harness.records.records
    assert record.change_type is ExpenseChangeType.EDITED
    assert record.expense_id == harness.expense.id
    assert record.actor_user_id == harness.user.id
    assert record.occurred_at == NOW
    assert set(record.changes) == {
        ExpenseFieldChange(ExpenseField.AMOUNT_MINOR_UNITS, 1500, 2000),
        ExpenseFieldChange(ExpenseField.CURRENCY, "USD", "EUR"),
        ExpenseFieldChange(ExpenseField.TRANSACTION_DATE, "2026-08-04", "2026-08-03"),
        ExpenseFieldChange(ExpenseField.MERCHANT, "Corner Shop", "Bakery"),
        ExpenseFieldChange(ExpenseField.CATEGORY_KEY, "groceries", "health"),
        ExpenseFieldChange(ExpenseField.NOTE, "milk", "bread"),
    }


def test_records_only_fields_whose_values_changed() -> None:
    harness = Harness()

    harness.edit(
        ExpenseEdit(
            # Same amount, new currency: only the currency changed.
            money=Money(minor_units=1500, currency=CurrencyCode("EUR")),
            merchant=MerchantName("Corner Shop"),
            note="oat milk",
        )
    )

    [record] = harness.records.records
    assert set(record.changes) == {
        ExpenseFieldChange(ExpenseField.CURRENCY, "USD", "EUR"),
        ExpenseFieldChange(ExpenseField.NOTE, "milk", "oat milk"),
    }


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(ExpenseEdit(), id="empty edit"),
        pytest.param(
            ExpenseEdit(
                money=Money(minor_units=1500, currency=CurrencyCode("USD")),
                transaction_date=TransactionDate(date(2026, 8, 4)),
                merchant=MerchantName("  Corner   Shop "),
                category_key="groceries",
                note="milk",
            ),
            id="same values resubmitted",
        ),
    ],
)
def test_no_op_edit_writes_nothing_and_keeps_modified_at(edit: ExpenseEdit) -> None:
    harness = Harness()

    result = harness.edit(edit)

    assert result == harness.expense
    assert result.modified_at == CREATED
    harness.assert_nothing_written()


def test_optional_fields_can_be_cleared() -> None:
    harness = Harness()

    edited = harness.edit(ExpenseEdit(merchant=None, note=None))

    assert edited.merchant is None
    assert edited.note is None
    [record] = harness.records.records
    assert set(record.changes) == {
        ExpenseFieldChange(ExpenseField.MERCHANT, "Corner Shop", None),
        ExpenseFieldChange(ExpenseField.NOTE, "milk", None),
    }


def test_another_owners_expense_is_not_found() -> None:
    harness = Harness()

    with pytest.raises(ExpenseNotFound):
        harness.edit(ExpenseEdit(note="x"), caller_id=uuid4())
    harness.assert_nothing_written()


def test_missing_expense_is_not_found() -> None:
    harness = Harness()

    with pytest.raises(ExpenseNotFound):
        harness.use_case.execute(
            expense_id=uuid4(), caller_id=harness.user.id, edit=ExpenseEdit(note="x")
        )
    harness.assert_nothing_written()


def test_deleted_expense_is_not_found() -> None:
    harness = Harness()
    harness.expenses.expense = harness.expense.delete(now=CREATED)

    with pytest.raises(ExpenseNotFound):
        harness.edit(ExpenseEdit(note="x"))
    harness.assert_nothing_written()


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            ExpenseEdit(money=Money(minor_units=0, currency=CurrencyCode("USD"))),
            "positive",
            id="zero amount",
        ),
        pytest.param(ExpenseEdit(category_key="not_a_category"), "category", id="unknown"),
        pytest.param(ExpenseEdit(category_key="retired"), "category", id="inactive category"),
        pytest.param(ExpenseEdit(category_key="Health"), "category", id="non-canonical key"),
        pytest.param(
            ExpenseEdit(transaction_date=TransactionDate(date(2026, 8, 8))),
            "future",
            id="date beyond tolerance",
        ),
    ],
)
def test_rejects_invalid_values_and_changes_nothing(edit: ExpenseEdit, message: str) -> None:
    harness = Harness()

    with pytest.raises(ExpenseEditRejected, match=message):
        harness.edit(edit)
    harness.assert_nothing_written()


def test_a_rejected_field_rejects_the_whole_edit() -> None:
    harness = Harness()

    with pytest.raises(ExpenseEditRejected):
        harness.edit(ExpenseEdit(note="valid", category_key="not_a_category"))
    harness.assert_nothing_written()


def test_date_tolerance_uses_the_owners_timezone() -> None:
    # 23:30 UTC on Aug 6 is already Aug 7 in Tokyo, so Aug 8 is "tomorrow" there.
    late_utc = datetime(2026, 8, 6, 23, 30, tzinfo=UTC)
    tokyo = Harness(timezone="Asia/Tokyo", now=late_utc)
    utc = Harness(timezone="UTC", now=late_utc)
    target = ExpenseEdit(transaction_date=TransactionDate(date(2026, 8, 8)))

    assert tokyo.edit(target).transaction_date == TransactionDate(date(2026, 8, 8))
    with pytest.raises(ExpenseEditRejected, match="future"):
        utc.edit(target)


@pytest.mark.parametrize(
    ("timezone", "now", "transaction_date", "accepted"),
    [
        ("UTC", datetime(2026, 8, 6, 23, 59, tzinfo=UTC), date(2026, 8, 7), True),
        ("UTC", datetime(2026, 8, 6, 23, 59, tzinfo=UTC), date(2026, 8, 8), False),
        ("UTC", datetime(2026, 8, 7, 0, 0, tzinfo=UTC), date(2026, 8, 8), True),
        ("America/Los_Angeles", datetime(2026, 8, 7, 6, 0, tzinfo=UTC), date(2026, 8, 8), False),
        ("America/Los_Angeles", datetime(2026, 8, 7, 7, 0, tzinfo=UTC), date(2026, 8, 8), True),
        ("UTC", datetime(2026, 8, 6, 12, 0, tzinfo=UTC), date(2020, 1, 1), True),
    ],
)
def test_shared_future_tolerance_rule_at_the_local_day_boundary(
    timezone: str, now: datetime, transaction_date: date, accepted: bool
) -> None:
    assert (
        is_within_future_tolerance(
            TransactionDate(transaction_date), now=now, timezone=Timezone(timezone)
        )
        is accepted
    )


def test_shared_rule_requires_an_aware_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_within_future_tolerance(
            TransactionDate(date(2026, 8, 6)),
            now=datetime(2026, 8, 6, 12, 0),
            timezone=Timezone("UTC"),
        )


def test_unchanged_date_is_not_rechecked_against_tolerance() -> None:
    # An edit that resubmits the current date alongside another change must
    # not fail, whatever the clock says.
    harness = Harness(now=CREATED - timedelta(days=30))

    edited = harness.edit(
        ExpenseEdit(transaction_date=TransactionDate(date(2026, 8, 4)), note="changed")
    )

    assert edited.note == "changed"
