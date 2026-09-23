from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

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
from mintflow.infrastructure.persistence.models import CaptureDraftRecord


def _to_record(draft: CaptureDraft) -> CaptureDraftRecord:
    return CaptureDraftRecord(
        id=draft.id,
        owner_id=draft.owner_id,
        source=draft.source.value,
        state=draft.state.value,
        revision=draft.revision,
        amount_minor_units=draft.amount.minor_units if draft.amount is not None else None,
        amount_currency=draft.amount.currency.value if draft.amount is not None else None,
        amount_source=draft.amount_source.value if draft.amount_source is not None else None,
        transaction_date=(
            draft.transaction_date.value if draft.transaction_date is not None else None
        ),
        transaction_date_source=(
            draft.transaction_date_source.value
            if draft.transaction_date_source is not None
            else None
        ),
        merchant_name=draft.merchant.value if draft.merchant is not None else None,
        merchant_source=(
            draft.merchant_source.value if draft.merchant_source is not None else None
        ),
        category_key=draft.category_key,
        category_key_source=(
            draft.category_key_source.value if draft.category_key_source is not None else None
        ),
        note=draft.note,
        expense_id=draft.expense_id,
        created_at=draft.created_at,
        modified_at=draft.modified_at,
        confirmed_at=draft.confirmed_at,
        receipt_id=draft.receipt_id,
        recognition_result_id=draft.recognition_result_id,
    )


def _record_values(draft: CaptureDraft) -> dict[str, object]:
    return {
        "owner_id": draft.owner_id,
        "source": draft.source.value,
        "state": draft.state.value,
        "revision": draft.revision,
        "amount_minor_units": (draft.amount.minor_units if draft.amount is not None else None),
        "amount_currency": (draft.amount.currency.value if draft.amount is not None else None),
        "amount_source": (draft.amount_source.value if draft.amount_source is not None else None),
        "transaction_date": (
            draft.transaction_date.value if draft.transaction_date is not None else None
        ),
        "transaction_date_source": (
            draft.transaction_date_source.value
            if draft.transaction_date_source is not None
            else None
        ),
        "merchant_name": draft.merchant.value if draft.merchant is not None else None,
        "merchant_source": (
            draft.merchant_source.value if draft.merchant_source is not None else None
        ),
        "category_key": draft.category_key,
        "category_key_source": (
            draft.category_key_source.value if draft.category_key_source is not None else None
        ),
        "note": draft.note,
        "expense_id": draft.expense_id,
        "modified_at": draft.modified_at,
        "confirmed_at": draft.confirmed_at,
        "recognition_result_id": draft.recognition_result_id,
    }


def _to_domain(record: CaptureDraftRecord) -> CaptureDraft:
    amount: Money | None = None
    if record.amount_minor_units is not None and record.amount_currency is not None:
        amount = Money(
            minor_units=record.amount_minor_units,
            currency=CurrencyCode(record.amount_currency),
        )
    return CaptureDraft(
        id=record.id,
        owner_id=record.owner_id,
        source=CaptureSource(record.source),
        state=CaptureDraftState(record.state),
        revision=record.revision,
        amount=amount,
        amount_source=(
            DraftFieldSource(record.amount_source) if record.amount_source is not None else None
        ),
        transaction_date=(
            TransactionDate(record.transaction_date)
            if record.transaction_date is not None
            else None
        ),
        transaction_date_source=(
            DraftFieldSource(record.transaction_date_source)
            if record.transaction_date_source is not None
            else None
        ),
        merchant=(MerchantName(record.merchant_name) if record.merchant_name is not None else None),
        merchant_source=(
            DraftFieldSource(record.merchant_source) if record.merchant_source is not None else None
        ),
        category_key=record.category_key,
        category_key_source=(
            DraftFieldSource(record.category_key_source)
            if record.category_key_source is not None
            else None
        ),
        note=record.note,
        expense_id=record.expense_id,
        created_at=record.created_at,
        modified_at=record.modified_at,
        confirmed_at=record.confirmed_at,
        receipt_id=record.receipt_id,
        recognition_result_id=record.recognition_result_id,
    )


class SqlAlchemyCaptureDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, draft: CaptureDraft, *, commit: bool = True) -> None:
        """Persist a new draft; ``commit=False`` joins the caller's transaction."""
        self._session.add(_to_record(draft))
        self._session.flush()
        if commit:
            self._session.commit()

    def get(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None:
        record = self._session.scalar(
            select(CaptureDraftRecord).where(
                CaptureDraftRecord.id == draft_id,
                CaptureDraftRecord.owner_id == owner_id,
            )
        )
        return _to_domain(record) if record is not None else None

    def get_for_update(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None:
        """Lock the draft row for a caller-managed multi-step transaction.

        Deliberately does not commit or otherwise close the transaction: a
        future confirmation use case must lock, decide, and write within one
        transaction. A caller that only reads leaves the row lock released
        when its session eventually commits, rolls back, or closes.
        """
        record = self._session.scalar(
            select(CaptureDraftRecord)
            .where(
                CaptureDraftRecord.id == draft_id,
                CaptureDraftRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        return _to_domain(record) if record is not None else None

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None:
        """Persist a new draft snapshot.

        ``commit=False`` lets a caller compose this with another write (for
        example ``SqlAlchemyExpenseRepository.create``) inside one
        transaction, such as the confirmation use case; the caller is then
        responsible for the final commit.
        """
        self._session.execute(
            update(CaptureDraftRecord)
            .where(CaptureDraftRecord.id == draft.id)
            .values(**_record_values(draft))
        )
        if commit:
            self._session.commit()
