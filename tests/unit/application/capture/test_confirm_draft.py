from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mintflow.application.capture import (
    CaptureDraftAccessDenied,
    CaptureDraftNotConfirmable,
    ConfirmCaptureDraft,
)
from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    Money,
    TransactionDate,
)
from mintflow.domain.user import Timezone, User

OWNER = uuid4()
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
MONEY = Money(minor_units=1500, currency=CurrencyCode("USD"))
DEFAULT_TRANSACTION_DATE = TransactionDate(date(2026, 8, 6))


class FakeDraftRepository:
    def __init__(self, draft: CaptureDraft | None) -> None:
        self.draft = draft
        self.updated: CaptureDraft | None = None
        self.update_calls: list[bool] = []
        self.get_for_update_calls: list[tuple[UUID, UUID]] = []

    def get_for_update(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None:
        self.get_for_update_calls.append((draft_id, owner_id))
        if self.draft is None or self.draft.id != draft_id or self.draft.owner_id != owner_id:
            return None
        return self.draft

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None:
        self.updated = draft
        self.update_calls.append(commit)


class FakeExpenseRepository:
    def __init__(self, existing: Expense | None = None) -> None:
        self.existing = existing
        self.created: Expense | None = None
        self.create_calls: list[bool] = []

    def create(self, expense: Expense, *, commit: bool = True) -> None:
        self.created = expense
        self.create_calls.append(commit)

    def get(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        if self.existing is not None and self.existing.id == expense_id:
            return self.existing
        return None


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self.user = user

    def get(self, user_id: UUID) -> User | None:
        return self.user


def _draft(
    *,
    state: str = "ready",
    amount: Money | None = MONEY,
    transaction_date: TransactionDate | None = DEFAULT_TRANSACTION_DATE,
    category_key: str | None = None,
    expense_id: UUID | None = None,
) -> CaptureDraft:
    draft = CaptureDraft.start(owner_id=OWNER, source=CaptureSource.WEB_MANUAL, now=NOW)
    if amount is not None:
        draft = draft.set_amount(caller_id=OWNER, amount=amount, now=NOW)
    if transaction_date is not None:
        draft = draft.set_transaction_date(
            caller_id=OWNER, transaction_date=transaction_date, now=NOW
        )
    if category_key is not None:
        draft = draft.set_category_key(caller_id=OWNER, category_key=category_key, now=NOW)
    if state == "collecting":
        return draft
    draft = draft.mark_ready_for_review(caller_id=OWNER, now=NOW)
    if state == "ready":
        return draft
    if state == "cancelled":
        return draft.cancel(caller_id=OWNER, now=NOW)
    if state == "expired":
        return draft.expire(now=NOW)
    if state == "confirmed":
        return draft.confirm(caller_id=OWNER, expense_id=expense_id or uuid4(), now=NOW)
    raise AssertionError(state)


def _user(timezone: str = "UTC") -> User:
    return User.create(now=NOW, timezone=Timezone(timezone))


def _use_case(
    draft: CaptureDraft | None,
    *,
    user: User | None = None,
    existing_expense: Expense | None = None,
    clock: datetime = NOW,
) -> tuple[ConfirmCaptureDraft, FakeDraftRepository, FakeExpenseRepository]:
    draft_repository = FakeDraftRepository(draft)
    expense_repository = FakeExpenseRepository(existing_expense)
    user_repository = FakeUserRepository(user if user is not None else _user())
    use_case = ConfirmCaptureDraft(
        draft_repository=draft_repository,
        expense_repository=expense_repository,
        user_repository=user_repository,
        clock=lambda: clock,
    )
    return use_case, draft_repository, expense_repository


def test_confirms_a_confirmable_draft_and_creates_exactly_one_expense() -> None:
    draft = _draft(category_key="groceries")
    use_case, draft_repository, expense_repository = _use_case(draft)

    expense = use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert expense.owner_id == OWNER
    assert expense.money == MONEY
    assert expense.category_key == "groceries"
    assert expense.capture_draft_id == draft.id
    assert expense_repository.created is expense
    assert expense_repository.create_calls == [False]
    assert draft_repository.updated is not None
    assert draft_repository.updated.state.value == "confirmed"
    assert draft_repository.updated.expense_id == expense.id
    assert draft_repository.update_calls == [True]


def test_falls_back_to_uncategorized_when_no_category_selected() -> None:
    draft = _draft(category_key=None)
    use_case, _, _ = _use_case(draft)

    expense = use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert expense.category_key == UNCATEGORIZED_KEY


def test_rejects_a_non_confirmable_draft_without_creating_an_expense() -> None:
    draft = _draft(state="collecting", transaction_date=None)
    use_case, draft_repository, expense_repository = _use_case(draft)

    with pytest.raises(CaptureDraftNotConfirmable):
        use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert expense_repository.created is None
    assert draft_repository.updated is None


@pytest.mark.parametrize("state", ["cancelled", "expired"])
def test_rejects_a_terminal_non_confirmed_draft(state: str) -> None:
    draft = _draft(state=state)
    use_case, draft_repository, expense_repository = _use_case(draft)

    with pytest.raises(CaptureDraftNotConfirmable):
        use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert expense_repository.created is None
    assert draft_repository.updated is None


def test_duplicate_confirmation_returns_existing_expense_with_no_writes() -> None:

    expense_id = uuid4()
    draft = _draft(state="confirmed", expense_id=expense_id)
    existing_expense = replace(
        Expense.create(
            owner_id=OWNER,
            money=MONEY,
            transaction_date=TransactionDate(date(2026, 8, 6)),
            category_key="groceries",
            capture_draft_id=draft.id,
            source=CaptureSource.WEB_MANUAL,
            now=NOW,
        ),
        id=expense_id,
    )
    use_case, draft_repository, expense_repository = _use_case(
        draft, existing_expense=existing_expense
    )

    result = use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert result is existing_expense
    assert expense_repository.created is None
    assert draft_repository.updated is None


def _confirmed_draft_with_expense() -> tuple[CaptureDraft, Expense]:
    expense_id = uuid4()
    draft = _draft(state="confirmed", expense_id=expense_id)
    expense = replace(
        Expense.create(
            owner_id=OWNER,
            money=MONEY,
            transaction_date=TransactionDate(date(2026, 8, 6)),
            category_key="groceries",
            capture_draft_id=draft.id,
            source=CaptureSource.WEB_MANUAL,
            now=NOW,
        ),
        id=expense_id,
    )
    return draft, expense


def test_duplicate_confirmation_of_a_deleted_expense_is_rejected_with_no_writes() -> None:
    draft, expense = _confirmed_draft_with_expense()
    use_case, draft_repository, expense_repository = _use_case(
        draft, existing_expense=expense.delete(now=NOW)
    )

    with pytest.raises(CaptureDraftNotConfirmable, match="deleted"):
        use_case.execute(draft_id=draft.id, caller_id=OWNER)
    assert expense_repository.created is None
    assert draft_repository.updated is None


def test_duplicate_confirmation_of_a_restored_expense_returns_it_again() -> None:
    draft, expense = _confirmed_draft_with_expense()
    restored = expense.delete(now=NOW).restore(now=NOW + timedelta(minutes=1))
    use_case, _draft_repository, expense_repository = _use_case(draft, existing_expense=restored)

    assert use_case.execute(draft_id=draft.id, caller_id=OWNER) is restored
    assert expense_repository.created is None


def test_rejects_a_caller_who_is_not_the_owner() -> None:
    draft = _draft()
    use_case, draft_repository, expense_repository = _use_case(draft)
    stranger = uuid4()

    with pytest.raises(CaptureDraftAccessDenied):
        use_case.execute(draft_id=draft.id, caller_id=stranger)

    assert expense_repository.created is None
    assert draft_repository.updated is None


def test_rejects_an_unknown_draft_id_the_same_way_as_wrong_owner() -> None:
    use_case, _, _ = _use_case(None)

    with pytest.raises(CaptureDraftAccessDenied):
        use_case.execute(draft_id=uuid4(), caller_id=OWNER)


@pytest.mark.parametrize(
    ("offset_days", "should_succeed"),
    [
        (0, True),
        (1, True),
        (2, False),
    ],
)
def test_future_date_rule_at_the_exact_boundary(offset_days: int, should_succeed: bool) -> None:

    transaction_date = TransactionDate(NOW.date() + timedelta(days=offset_days))
    draft = _draft(transaction_date=transaction_date, category_key="groceries")
    use_case, _, _ = _use_case(draft, user=_user("UTC"))

    if should_succeed:
        expense = use_case.execute(draft_id=draft.id, caller_id=OWNER)
        assert expense.transaction_date == transaction_date
    else:
        with pytest.raises(CaptureDraftNotConfirmable, match="future"):
            use_case.execute(draft_id=draft.id, caller_id=OWNER)


def test_future_date_rule_uses_the_owners_timezone_not_utc() -> None:

    # 23:00 UTC on Aug 6 is already Aug 7 in a UTC+2 timezone.
    clock_time = datetime(2026, 8, 6, 23, 0, tzinfo=UTC)
    transaction_date = TransactionDate(date(2026, 8, 8))
    draft = _draft(transaction_date=transaction_date, category_key="groceries")
    use_case, _, _ = _use_case(draft, user=_user("Europe/Warsaw"), clock=clock_time)

    expense = use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert expense.transaction_date == transaction_date


def test_a_receipt_draft_carries_its_receipt_into_the_expense() -> None:
    receipt_id = uuid4()
    draft = replace(_draft(), receipt_id=receipt_id, source=CaptureSource.TELEGRAM_RECEIPT)
    use_case, _draft_repository, expense_repository = _use_case(draft)

    expense = use_case.execute(draft_id=draft.id, caller_id=OWNER)

    assert expense.receipt_id == receipt_id
    assert expense.source is CaptureSource.TELEGRAM_RECEIPT


def test_a_manual_draft_creates_an_expense_without_a_receipt() -> None:
    draft = _draft()
    use_case, _draft_repository, _expense_repository = _use_case(draft)

    assert use_case.execute(draft_id=draft.id, caller_id=OWNER).receipt_id is None
