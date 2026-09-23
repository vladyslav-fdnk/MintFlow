from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureDraftState,
    CaptureSource,
    CurrencyCode,
    DraftFieldSource,
    MerchantName,
    Money,
    Receipt,
    ReceiptState,
    RecognitionResult,
    TransactionDate,
)

OWNER = uuid4()
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=2)
EUR = CurrencyCode("EUR")


# --- Receipt lifecycle -----------------------------------------------------------------------


def test_a_received_receipt_is_queued() -> None:
    receipt = Receipt.receive(owner_id=OWNER, now=NOW)

    assert receipt.state is ReceiptState.QUEUED
    assert receipt.attempt_id is None
    assert receipt.is_claimable(now=NOW)


def test_claim_starts_an_attempt_with_a_lease() -> None:
    claimed = Receipt.receive(owner_id=OWNER, now=NOW).claim(now=NOW, lease=LEASE)

    assert claimed.state is ReceiptState.PROCESSING
    assert claimed.attempt_id is not None
    assert claimed.lease_expires_at == NOW + LEASE
    assert not claimed.is_claimable(now=NOW + LEASE - timedelta(seconds=1))


def test_an_expired_lease_can_be_taken_over_with_a_new_attempt() -> None:
    first = Receipt.receive(owner_id=OWNER, now=NOW).claim(now=NOW, lease=LEASE)

    second = first.claim(now=NOW + LEASE, lease=LEASE)

    assert second.attempt_id != first.attempt_id
    assert first.attempt_id is not None
    with pytest.raises(ValueError, match="current attempt"):
        second.complete(attempt_id=first.attempt_id, now=NOW + LEASE)


@pytest.mark.parametrize("finish", ["complete", "fail"])
def test_only_the_current_attempt_finishes_and_only_once(finish: str) -> None:
    claimed = Receipt.receive(owner_id=OWNER, now=NOW).claim(now=NOW, lease=LEASE)
    assert claimed.attempt_id is not None

    finished = getattr(claimed, finish)(attempt_id=claimed.attempt_id, now=NOW)

    expected = ReceiptState.RECOGNIZED if finish == "complete" else ReceiptState.RECOGNITION_FAILED
    assert finished.state is expected
    assert finished.lease_expires_at is None
    with pytest.raises(ValueError):
        getattr(finished, finish)(attempt_id=claimed.attempt_id, now=NOW)
    with pytest.raises(ValueError):
        getattr(claimed, finish)(attempt_id=uuid4(), now=NOW)


@pytest.mark.parametrize("state", [ReceiptState.RECOGNIZED, ReceiptState.RECOGNITION_FAILED])
def test_finished_receipts_are_not_claimable(state: ReceiptState) -> None:
    claimed = Receipt.receive(owner_id=OWNER, now=NOW).claim(now=NOW, lease=LEASE)
    assert claimed.attempt_id is not None
    finished = (
        claimed.complete(attempt_id=claimed.attempt_id, now=NOW)
        if state is ReceiptState.RECOGNIZED
        else claimed.fail(attempt_id=claimed.attempt_id, now=NOW)
    )

    with pytest.raises(ValueError, match="not available"):
        finished.claim(now=NOW + timedelta(days=1), lease=LEASE)


def test_a_removed_image_can_never_be_processed() -> None:
    removed = Receipt.receive(owner_id=OWNER, now=NOW).remove_image(now=NOW)

    assert removed.remove_image(now=NOW + timedelta(days=1)) == removed
    with pytest.raises(ValueError, match="not available"):
        removed.claim(now=NOW, lease=LEASE)


def test_claim_rejects_a_non_positive_lease() -> None:
    with pytest.raises(ValueError, match="lease"):
        Receipt.receive(owner_id=OWNER, now=NOW).claim(now=NOW, lease=timedelta(0))


# --- RecognitionResult -----------------------------------------------------------------------


def _claimed() -> Receipt:
    return Receipt.receive(owner_id=OWNER, now=NOW).claim(now=NOW, lease=LEASE)


def _result(
    receipt: Receipt,
    *,
    merchant: str | None = "Corner Shop",
    day: date | None = date(2026, 9, 20),
    total: int | None = 1250,
) -> RecognitionResult:
    assert receipt.attempt_id is not None
    return RecognitionResult.for_attempt(
        receipt=receipt,
        attempt_id=receipt.attempt_id,
        merchant=MerchantName(merchant) if merchant else None,
        transaction_date=TransactionDate(day) if day else None,
        total=Money(minor_units=total, currency=EUR) if total else None,
        now=NOW,
    )


def test_a_result_belongs_to_its_receipt_attempt_and_owner() -> None:
    receipt = _claimed()

    result = _result(receipt)

    assert (result.receipt_id, result.attempt_id, result.owner_id) == (
        receipt.id,
        receipt.attempt_id,
        OWNER,
    )
    assert not result.is_empty
    assert _result(receipt, merchant=None, day=None, total=None).is_empty


# --- applying a result to a draft ------------------------------------------------------------


def _receipt_draft(receipt: Receipt) -> CaptureDraft:
    return CaptureDraft.start_from_receipt(owner_id=OWNER, receipt_id=receipt.id, now=NOW)


def _apply(draft: CaptureDraft, result: RecognitionResult) -> CaptureDraft:
    return draft.apply_recognition(result=result, fallback_category_key=UNCATEGORIZED_KEY, now=NOW)


def test_a_receipt_draft_waits_for_recognition() -> None:
    receipt = _claimed()

    draft = _receipt_draft(receipt)

    assert draft.state is CaptureDraftState.AWAITING_RECOGNITION
    assert draft.source is CaptureSource.TELEGRAM_RECEIPT
    assert draft.receipt_id == receipt.id


def test_a_complete_result_fills_every_field_and_moves_to_review() -> None:
    receipt = _claimed()
    result = _result(receipt)

    draft = _apply(_receipt_draft(receipt), result)

    assert draft.state is CaptureDraftState.READY_FOR_REVIEW
    assert draft.amount == Money(minor_units=1250, currency=EUR)
    assert draft.transaction_date == TransactionDate(date(2026, 9, 20))
    assert draft.merchant == MerchantName("Corner Shop")
    assert {draft.amount_source, draft.transaction_date_source, draft.merchant_source} == {
        DraftFieldSource.RECOGNITION
    }
    assert (draft.category_key, draft.category_key_source) == (
        UNCATEGORIZED_KEY,
        DraftFieldSource.DEFAULT,
    )
    assert draft.recognition_result_id == result.id
    assert draft.revision == 1


def test_a_partial_result_moves_to_collecting_for_the_missing_fields() -> None:
    receipt = _claimed()

    draft = _apply(_receipt_draft(receipt), _result(receipt, total=None))

    assert draft.state is CaptureDraftState.COLLECTING
    assert draft.amount is None
    assert draft.merchant == MerchantName("Corner Shop")


def test_user_supplied_fields_are_never_overwritten() -> None:
    receipt = _claimed()
    draft = _receipt_draft(receipt)
    draft = draft.set_amount(
        caller_id=OWNER, amount=Money(minor_units=999, currency=CurrencyCode("USD")), now=NOW
    )
    draft = draft.set_merchant(caller_id=OWNER, merchant=MerchantName("Mine"), now=NOW)
    draft = draft.set_category_key(caller_id=OWNER, category_key="transport", now=NOW)

    applied = _apply(draft, _result(receipt))

    assert applied.amount == Money(minor_units=999, currency=CurrencyCode("USD"))
    assert applied.merchant == MerchantName("Mine")
    assert (applied.category_key, applied.category_key_source) == (
        "transport",
        DraftFieldSource.USER,
    )
    assert applied.transaction_date_source is DraftFieldSource.RECOGNITION


def test_defaulted_fields_are_replaced_by_recognition() -> None:
    receipt = _claimed()
    draft = _receipt_draft(receipt).set_transaction_date(
        caller_id=OWNER,
        transaction_date=TransactionDate(date(2026, 9, 23)),
        now=NOW,
        source=DraftFieldSource.DEFAULT,
    )

    applied = _apply(draft, _result(receipt))

    assert applied.transaction_date == TransactionDate(date(2026, 9, 20))


def test_a_late_result_after_manual_entry_fills_only_untouched_fields_and_keeps_the_state() -> None:
    receipt = _claimed()
    draft = _receipt_draft(receipt).continue_manually(caller_id=OWNER, now=NOW)
    draft = draft.set_amount(caller_id=OWNER, amount=Money(minor_units=500, currency=EUR), now=NOW)
    assert draft.state is CaptureDraftState.COLLECTING

    applied = _apply(draft, _result(receipt))

    assert applied.state is CaptureDraftState.COLLECTING
    assert applied.amount == Money(minor_units=500, currency=EUR)
    assert applied.merchant == MerchantName("Corner Shop")


def test_a_result_from_another_receipt_or_owner_is_rejected() -> None:
    receipt, other = _claimed(), _claimed()
    draft = _receipt_draft(receipt)
    foreign_owner = RecognitionResult.for_attempt(
        receipt=Receipt.receive(owner_id=uuid4(), now=NOW),
        attempt_id=uuid4(),
        merchant=None,
        transaction_date=None,
        total=None,
        now=NOW,
    )

    with pytest.raises(ValueError, match="another draft"):
        _apply(draft, _result(other))
    with pytest.raises(ValueError, match="another draft"):
        _apply(draft, foreign_owner)


@pytest.mark.parametrize("finish", ["cancel", "confirmed"])
def test_a_result_cannot_change_a_finished_draft(finish: str) -> None:
    receipt = _claimed()
    draft = _apply(_receipt_draft(receipt), _result(receipt))
    if finish == "cancel":
        finished = draft.cancel(caller_id=OWNER, now=NOW)
    else:
        finished = draft.confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW)

    with pytest.raises(ValueError, match="cannot be edited"):
        _apply(finished, _result(receipt))


def test_continue_manually_only_leaves_awaiting_recognition() -> None:
    receipt = _claimed()
    waiting = _receipt_draft(receipt)

    manual = waiting.continue_manually(caller_id=OWNER, now=NOW)

    assert manual.state is CaptureDraftState.COLLECTING
    assert manual.continue_manually(caller_id=OWNER, now=NOW) is manual
    with pytest.raises(ValueError, match="owner"):
        waiting.continue_manually(caller_id=uuid4(), now=NOW)


def test_recognition_provenance_is_reserved_for_apply_recognition() -> None:
    draft = _receipt_draft(_claimed())

    with pytest.raises(ValueError, match="apply_recognition"):
        draft.set_merchant(
            caller_id=OWNER,
            merchant=MerchantName("x"),
            now=NOW,
            source=DraftFieldSource.RECOGNITION,
        )


def test_a_waiting_receipt_draft_can_be_cancelled_and_expired() -> None:
    draft = _receipt_draft(_claimed())

    assert draft.cancel(caller_id=OWNER, now=NOW).state is CaptureDraftState.CANCELLED
    assert draft.expire(now=NOW).state is CaptureDraftState.EXPIRED


def test_manual_drafts_have_no_receipt() -> None:
    draft = CaptureDraft.start(owner_id=OWNER, source=CaptureSource.TELEGRAM_MANUAL, now=NOW)

    assert (draft.receipt_id, draft.recognition_result_id) == (None, None)
    assert draft.state is CaptureDraftState.COLLECTING
