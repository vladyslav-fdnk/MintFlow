from datetime import UTC, date, datetime
from uuid import uuid4

from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    DraftFieldSource,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.infrastructure.persistence.capture_drafts import _to_domain, _to_record

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
OWNER = uuid4()


def _fully_populated_draft() -> CaptureDraft:
    draft = CaptureDraft.start(owner_id=OWNER, source=CaptureSource.WEB_MANUAL, now=NOW)
    draft = draft.set_amount(
        caller_id=OWNER,
        amount=Money(minor_units=1234, currency=CurrencyCode("USD")),
        now=NOW,
    )
    draft = draft.set_transaction_date(
        caller_id=OWNER,
        transaction_date=TransactionDate(date(2026, 8, 3)),
        now=NOW,
        source=DraftFieldSource.DEFAULT,
    )
    draft = draft.set_merchant(caller_id=OWNER, merchant=MerchantName("Coffee Shop"), now=NOW)
    draft = draft.set_category_key(caller_id=OWNER, category_key="groceries", now=NOW)
    draft = draft.set_note(caller_id=OWNER, note="Team lunch", now=NOW)
    draft = draft.mark_ready_for_review(caller_id=OWNER, now=NOW)
    return draft.confirm(caller_id=OWNER, expense_id=uuid4(), now=NOW)


def test_round_trips_a_fully_populated_draft() -> None:
    draft = _fully_populated_draft()

    record = _to_record(draft)
    restored = _to_domain(record)

    assert restored == draft


def test_round_trips_a_draft_with_only_mandatory_eligible_fields() -> None:
    draft = CaptureDraft.start(owner_id=OWNER, source=CaptureSource.WEB_MANUAL, now=NOW)
    draft = draft.set_amount(
        caller_id=OWNER, amount=Money(minor_units=500, currency=CurrencyCode("EUR")), now=NOW
    )
    draft = draft.set_transaction_date(
        caller_id=OWNER, transaction_date=TransactionDate(date(2026, 8, 4)), now=NOW
    )

    record = _to_record(draft)
    restored = _to_domain(record)

    assert restored == draft
    assert restored.merchant is None
    assert restored.category_key is None
    assert restored.note is None
