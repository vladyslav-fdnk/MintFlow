"""Receipt persistence: the work queue, image bytes, and recognition results (design R2, R3).

Only ``claim_next`` commits, so other workers see the claim at once. Every other
write joins the caller's transaction (``claim_delay_notices`` included, so the
caller commits the claim before sending anything): the worker stores a result, finishes the
attempt, and updates the draft together.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from mintflow.domain.capture import (
    CurrencyCode,
    MerchantName,
    Money,
    Receipt,
    ReceiptState,
    RecognitionResult,
    TransactionDate,
)
from mintflow.infrastructure.persistence.models import (
    ReceiptImageRecord,
    ReceiptRecord,
    RecognitionResultRecord,
)


def _receipt(record: ReceiptRecord) -> Receipt:
    return Receipt(
        id=record.id,
        owner_id=record.owner_id,
        state=ReceiptState(record.state),
        attempt_id=record.attempt_id,
        lease_expires_at=record.lease_expires_at,
        image_removed_at=record.image_removed_at,
        created_at=record.created_at,
        modified_at=record.modified_at,
    )


def _result(record: RecognitionResultRecord) -> RecognitionResult:
    total = (
        Money(minor_units=record.total_minor_units, currency=CurrencyCode(record.total_currency))
        if record.total_minor_units is not None and record.total_currency is not None
        else None
    )
    return RecognitionResult(
        id=record.id,
        receipt_id=record.receipt_id,
        attempt_id=record.attempt_id,
        owner_id=record.owner_id,
        merchant=MerchantName(record.merchant_name) if record.merchant_name is not None else None,
        transaction_date=(
            TransactionDate(record.transaction_date)
            if record.transaction_date is not None
            else None
        ),
        total=total,
        created_at=record.created_at,
    )


@dataclass(frozen=True, slots=True)
class StoredImage:
    media_type: str
    content: bytes


class SqlAlchemyReceiptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, receipt: Receipt, *, telegram_file_id: str | None) -> None:
        self._session.add(
            ReceiptRecord(
                id=receipt.id,
                owner_id=receipt.owner_id,
                state=receipt.state.value,
                attempt_id=receipt.attempt_id,
                lease_expires_at=receipt.lease_expires_at,
                telegram_file_id=telegram_file_id,
                image_removed_at=receipt.image_removed_at,
                created_at=receipt.created_at,
                modified_at=receipt.modified_at,
            )
        )
        self._session.flush()

    def get(self, *, receipt_id: UUID) -> Receipt | None:
        record = self._session.get(ReceiptRecord, receipt_id, populate_existing=True)
        return _receipt(record) if record is not None else None

    def telegram_file_id(self, *, receipt_id: UUID) -> str | None:
        return self._session.scalar(
            select(ReceiptRecord.telegram_file_id).where(ReceiptRecord.id == receipt_id)
        )

    def claim_next(self, *, now: datetime, lease: timedelta) -> Receipt | None:
        """Claim the oldest queued receipt, or one whose lease expired, and commit at once."""
        try:
            record = self._session.scalar(
                select(ReceiptRecord)
                .where(
                    ReceiptRecord.image_removed_at.is_(None),
                    or_(
                        ReceiptRecord.state == ReceiptState.QUEUED.value,
                        and_(
                            ReceiptRecord.state == ReceiptState.PROCESSING.value,
                            ReceiptRecord.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(ReceiptRecord.created_at, ReceiptRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                self._session.commit()
                return None
            claimed = _receipt(record).claim(now=now, lease=lease)
            record.state = claimed.state.value
            record.attempt_id = claimed.attempt_id
            record.lease_expires_at = claimed.lease_expires_at
            record.modified_at = claimed.modified_at
            self._session.commit()
            return claimed
        except BaseException:
            self._session.rollback()
            raise

    def claim_delay_notices(
        self, *, now: datetime, received_before: datetime, limit: int = 20
    ) -> list[tuple[UUID, UUID]]:
        """Mark unfinished receipts older than ``received_before`` as notified, once each.

        Returns ``(receipt_id, owner_id)`` pairs. Rows locked by another worker are skipped
        and a receipt is never returned twice, so its delay message is sent at most once.
        """
        overdue = (
            select(ReceiptRecord.id)
            .where(
                ReceiptRecord.state.in_((ReceiptState.QUEUED.value, ReceiptState.PROCESSING.value)),
                ReceiptRecord.image_removed_at.is_(None),
                ReceiptRecord.delay_notice_sent_at.is_(None),
                ReceiptRecord.created_at <= received_before,
            )
            .order_by(ReceiptRecord.created_at, ReceiptRecord.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .scalar_subquery()
        )
        rows = self._session.execute(
            update(ReceiptRecord)
            .where(ReceiptRecord.id.in_(overdue))
            .values(delay_notice_sent_at=now)
            .returning(ReceiptRecord.id, ReceiptRecord.owner_id)
        )
        return [(row.id, row.owner_id) for row in rows]

    def save_finished(self, receipt: Receipt) -> bool:
        """Record a completed or failed attempt, only if it is still the current one."""
        updated = self._session.scalar(
            update(ReceiptRecord)
            .where(
                ReceiptRecord.id == receipt.id,
                ReceiptRecord.attempt_id == receipt.attempt_id,
                ReceiptRecord.state == ReceiptState.PROCESSING.value,
            )
            .values(
                state=receipt.state.value,
                lease_expires_at=receipt.lease_expires_at,
                modified_at=receipt.modified_at,
            )
            .returning(ReceiptRecord.id)
        )
        return updated is not None

    def save_result(self, result: RecognitionResult) -> None:
        self._session.add(
            RecognitionResultRecord(
                id=result.id,
                receipt_id=result.receipt_id,
                attempt_id=result.attempt_id,
                owner_id=result.owner_id,
                merchant_name=result.merchant.value if result.merchant is not None else None,
                transaction_date=(
                    result.transaction_date.value if result.transaction_date is not None else None
                ),
                total_minor_units=result.total.minor_units if result.total is not None else None,
                total_currency=result.total.currency.value if result.total is not None else None,
                created_at=result.created_at,
            )
        )
        self._session.flush()

    def get_result(self, *, result_id: UUID) -> RecognitionResult | None:
        record = self._session.get(RecognitionResultRecord, result_id)
        return _result(record) if record is not None else None


class SqlAlchemyReceiptImageStore:
    """Image bytes in PostgreSQL behind a small store interface (design R3)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def put(self, *, receipt_id: UUID, media_type: str, content: bytes, now: datetime) -> None:
        self._session.add(
            ReceiptImageRecord(
                receipt_id=receipt_id, media_type=media_type, content=content, stored_at=now
            )
        )
        self._session.flush()

    def get(self, *, receipt_id: UUID) -> StoredImage | None:
        row = self._session.execute(
            select(ReceiptImageRecord.media_type, ReceiptImageRecord.content).where(
                ReceiptImageRecord.receipt_id == receipt_id
            )
        ).one_or_none()
        return StoredImage(media_type=row.media_type, content=row.content) if row else None
