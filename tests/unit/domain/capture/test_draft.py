from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from mintflow.domain.capture import (
    CaptureDraft,
    CaptureDraftState,
    CaptureSource,
    CurrencyCode,
    DraftFieldSource,
    MerchantName,
    Money,
    TransactionDate,
)

OWNER = uuid4()
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
MONEY = Money(minor_units=1000, currency=CurrencyCode("USD"))
TX_DATE = TransactionDate(date(2026, 7, 31))


def _draft() -> CaptureDraft:
    return CaptureDraft.start(owner_id=OWNER, source=CaptureSource.WEB_MANUAL, now=NOW)


def _ready(draft: CaptureDraft | None = None) -> CaptureDraft:
    draft = draft if draft is not None else _draft()
    draft = draft.set_amount(caller_id=OWNER, amount=MONEY, now=NOW)
    draft = draft.set_transaction_date(caller_id=OWNER, transaction_date=TX_DATE, now=NOW)
    return draft.mark_ready_for_review(caller_id=OWNER, now=NOW)


def test_new_draft_starts_collecting_with_no_fields_set() -> None:
    draft = _draft()

    assert draft.state is CaptureDraftState.COLLECTING
    assert draft.revision == 0
    assert draft.amount is None
    assert draft.amount_source is None
    assert draft.transaction_date is None
    assert draft.merchant is None
    assert draft.category_key is None
    assert draft.note is None
    assert draft.expense_id is None
    assert draft.confirmed_at is None


def test_setting_amount_updates_value_provenance_and_revision() -> None:
    draft = _draft()

    updated = draft.set_amount(caller_id=OWNER, amount=MONEY, now=NOW)

    assert updated.amount == MONEY
    assert updated.amount_source is DraftFieldSource.USER
    assert updated.revision == 1
    # original instance is untouched (frozen dataclass immutability)
    assert draft.amount is None
    assert draft.revision == 0


def test_overwriting_a_field_keeps_only_the_latest_value_and_provenance() -> None:
    draft = _draft().set_amount(caller_id=OWNER, amount=MONEY, now=NOW)
    other_money = Money(minor_units=2500, currency=CurrencyCode("USD"))

    updated = draft.set_amount(
        caller_id=OWNER,
        amount=other_money,
        now=NOW,
        source=DraftFieldSource.DEFAULT,
    )

    assert updated.amount == other_money
    assert updated.amount_source is DraftFieldSource.DEFAULT
    assert updated.revision == 2


def test_default_provenance_is_producible_for_transaction_date() -> None:
    draft = _draft()

    updated = draft.set_transaction_date(
        caller_id=OWNER,
        transaction_date=TX_DATE,
        now=NOW,
        source=DraftFieldSource.DEFAULT,
    )

    assert updated.transaction_date_source is DraftFieldSource.DEFAULT


@pytest.mark.parametrize(
    "setter_name",
    ["set_amount", "set_transaction_date", "set_merchant", "set_category_key"],
)
def test_no_setter_can_produce_recognition_provenance(setter_name: str) -> None:
    draft = _draft()
    setter = getattr(draft, setter_name)
    field_kwargs: dict[str, object] = {}
    if setter_name == "set_amount":
        field_kwargs = {"amount": MONEY}
    elif setter_name == "set_transaction_date":
        field_kwargs = {"transaction_date": TX_DATE}
    elif setter_name == "set_merchant":
        field_kwargs = {"merchant": MerchantName("Coffee Shop")}
    elif setter_name == "set_category_key":
        field_kwargs = {"category_key": "groceries"}

    with pytest.raises(ValueError, match="recognition provenance"):
        setter(caller_id=OWNER, now=NOW, source=DraftFieldSource.RECOGNITION, **field_kwargs)


def test_set_category_key_rejects_non_stable_key() -> None:
    draft = _draft()

    with pytest.raises(ValueError, match="stable lowercase identifier"):
        draft.set_category_key(caller_id=OWNER, category_key="Groceries", now=NOW)
    with pytest.raises(ValueError, match="stable lowercase identifier"):
        draft.set_category_key(caller_id=OWNER, category_key="", now=NOW)


def test_only_the_owner_can_mutate_the_draft() -> None:
    draft = _draft()
    stranger = uuid4()

    with pytest.raises(ValueError, match="not the owner"):
        draft.set_amount(caller_id=stranger, amount=MONEY, now=NOW)
    with pytest.raises(ValueError, match="not the owner"):
        draft.mark_ready_for_review(caller_id=stranger, now=NOW)
    with pytest.raises(ValueError, match="not the owner"):
        draft.cancel(caller_id=stranger, now=NOW)
    with pytest.raises(ValueError, match="not the owner"):
        draft.confirm(caller_id=stranger, expense_id=uuid4(), now=NOW)


def test_mark_ready_for_review_succeeds_only_from_collecting() -> None:
    draft = _draft()

    ready = draft.mark_ready_for_review(caller_id=OWNER, now=NOW)
    assert ready.state is CaptureDraftState.READY_FOR_REVIEW

    with pytest.raises(ValueError, match="cannot mark ready for review"):
        ready.mark_ready_for_review(caller_id=OWNER, now=NOW)


@pytest.mark.parametrize(
    ("has_amount", "has_date", "state", "expected"),
    [
        (True, True, CaptureDraftState.READY_FOR_REVIEW, True),
        (False, True, CaptureDraftState.READY_FOR_REVIEW, False),
        (True, False, CaptureDraftState.READY_FOR_REVIEW, False),
        (True, True, CaptureDraftState.COLLECTING, False),
    ],
)
def test_is_confirmable_requires_mandatory_fields_and_ready_state(
    has_amount: bool, has_date: bool, state: CaptureDraftState, expected: bool
) -> None:
    draft = _draft()
    if has_amount:
        draft = draft.set_amount(caller_id=OWNER, amount=MONEY, now=NOW)
    if has_date:
        draft = draft.set_transaction_date(caller_id=OWNER, transaction_date=TX_DATE, now=NOW)
    if state is CaptureDraftState.READY_FOR_REVIEW:
        draft = draft.mark_ready_for_review(caller_id=OWNER, now=NOW)

    assert draft.is_confirmable() is expected


def test_confirm_succeeds_on_a_confirmable_ready_draft() -> None:
    draft = _ready()
    expense_id = uuid4()

    confirmed = draft.confirm(caller_id=OWNER, expense_id=expense_id, now=NOW)

    assert confirmed.state is CaptureDraftState.CONFIRMED
    assert confirmed.expense_id == expense_id
    assert confirmed.confirmed_at == NOW


def test_confirm_fails_on_a_non_confirmable_draft() -> None:
    draft = _draft()

    with pytest.raises(ValueError, match="not confirmable"):
        draft.confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW)


def test_confirm_cannot_be_called_twice() -> None:
    confirmed = _ready().confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW)

    with pytest.raises(ValueError, match="not confirmable"):
        confirmed.confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW + timedelta(minutes=1))


@pytest.mark.parametrize("build_state", ["confirmed", "cancelled", "expired"])
def test_cancel_fails_on_a_terminal_draft(build_state: str) -> None:
    draft = _terminal_draft(build_state)

    with pytest.raises(ValueError, match="cannot cancel"):
        draft.cancel(caller_id=OWNER, now=NOW)


@pytest.mark.parametrize("build_state", ["confirmed", "cancelled", "expired"])
def test_expire_fails_on_a_terminal_draft(build_state: str) -> None:
    draft = _terminal_draft(build_state)

    with pytest.raises(ValueError, match="cannot expire"):
        draft.expire(now=NOW)


def _terminal_draft(kind: str) -> CaptureDraft:
    if kind == "confirmed":
        return _ready().confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW)
    if kind == "cancelled":
        return _draft().cancel(caller_id=OWNER, now=NOW)
    if kind == "expired":
        return _draft().expire(now=NOW)
    raise AssertionError(kind)


def test_cancel_and_expire_succeed_from_open_states() -> None:
    assert _draft().cancel(caller_id=OWNER, now=NOW).state is CaptureDraftState.CANCELLED
    assert _draft().expire(now=NOW).state is CaptureDraftState.EXPIRED
    assert (
        _draft()
        .mark_ready_for_review(caller_id=OWNER, now=NOW)
        .cancel(caller_id=OWNER, now=NOW)
        .state
        is CaptureDraftState.CANCELLED
    )


def test_editing_a_terminal_draft_is_rejected() -> None:
    confirmed = _ready().confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW)

    with pytest.raises(ValueError, match="cannot be edited"):
        confirmed.set_amount(caller_id=OWNER, amount=MONEY, now=NOW)


def test_transitions_never_mutate_the_original_instance() -> None:
    draft = _draft()
    original_revision = draft.revision
    original_state = draft.state

    draft.set_amount(caller_id=OWNER, amount=MONEY, now=NOW)
    draft.mark_ready_for_review(caller_id=OWNER, now=NOW)

    assert draft.revision == original_revision
    assert draft.state == original_state
