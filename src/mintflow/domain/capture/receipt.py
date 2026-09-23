"""Receipt and RecognitionResult (docs/domain_design_proposal.md; receipt_recognition_design.md).

A Receipt is a supporting aggregate: ownership, a processing lifecycle, and
image removal. Each processing run has its own attempt id, so a slow or crashed
attempt can never complete after a newer one started. A RecognitionResult is
an immutable snapshot of the values MintFlow selected from one attempt; it
carries no recognition-provider types.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from mintflow.domain.capture.money import Money
from mintflow.domain.capture.values import MerchantName, TransactionDate


def _utc(now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC)


class ReceiptState(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RECOGNIZED = "recognized"
    RECOGNITION_FAILED = "recognition_failed"


@dataclass(frozen=True, slots=True)
class Receipt:
    id: UUID
    owner_id: UUID
    state: ReceiptState
    attempt_id: UUID | None
    lease_expires_at: datetime | None
    image_removed_at: datetime | None
    created_at: datetime
    modified_at: datetime

    @classmethod
    def receive(cls, *, owner_id: UUID, now: datetime) -> "Receipt":
        received_at = _utc(now)
        return cls(
            id=uuid4(),
            owner_id=owner_id,
            state=ReceiptState.QUEUED,
            attempt_id=None,
            lease_expires_at=None,
            image_removed_at=None,
            created_at=received_at,
            modified_at=received_at,
        )

    def is_claimable(self, *, now: datetime) -> bool:
        if self.image_removed_at is not None:
            return False
        if self.state is ReceiptState.QUEUED:
            return True
        return (
            self.state is ReceiptState.PROCESSING
            and self.lease_expires_at is not None
            and self.lease_expires_at <= _utc(now)
        )

    def claim(self, *, now: datetime, lease: timedelta) -> "Receipt":
        """Start a new processing attempt; an expired lease may be taken over."""
        if not self.is_claimable(now=now):
            raise ValueError("receipt is not available for processing")
        if lease <= timedelta(0):
            raise ValueError("lease must be positive")
        claimed_at = _utc(now)
        return replace(
            self,
            state=ReceiptState.PROCESSING,
            attempt_id=uuid4(),
            lease_expires_at=claimed_at + lease,
            modified_at=claimed_at,
        )

    def _finish(self, *, attempt_id: UUID, now: datetime, state: ReceiptState) -> "Receipt":
        if self.state is not ReceiptState.PROCESSING or self.attempt_id != attempt_id:
            raise ValueError("attempt is not the receipt's current attempt")
        return replace(self, state=state, lease_expires_at=None, modified_at=_utc(now))

    def complete(self, *, attempt_id: UUID, now: datetime) -> "Receipt":
        return self._finish(attempt_id=attempt_id, now=now, state=ReceiptState.RECOGNIZED)

    def fail(self, *, attempt_id: UUID, now: datetime) -> "Receipt":
        return self._finish(attempt_id=attempt_id, now=now, state=ReceiptState.RECOGNITION_FAILED)

    def remove_image(self, *, now: datetime) -> "Receipt":
        """Idempotent. A receipt without its image can never be processed again."""
        if self.image_removed_at is not None:
            return self
        removed_at = _utc(now)
        return replace(self, image_removed_at=removed_at, modified_at=removed_at)


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """The values selected from one attempt; any of them may be missing."""

    id: UUID
    receipt_id: UUID
    attempt_id: UUID
    owner_id: UUID
    merchant: MerchantName | None
    transaction_date: TransactionDate | None
    total: Money | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.total is not None and self.total.minor_units <= 0:
            raise ValueError("a recognized total must be positive")
        object.__setattr__(self, "created_at", _utc(self.created_at))

    @classmethod
    def for_attempt(
        cls,
        *,
        receipt: Receipt,
        attempt_id: UUID,
        merchant: MerchantName | None,
        transaction_date: TransactionDate | None,
        total: Money | None,
        now: datetime,
    ) -> "RecognitionResult":
        return cls(
            id=uuid4(),
            receipt_id=receipt.id,
            attempt_id=attempt_id,
            owner_id=receipt.owner_id,
            merchant=merchant,
            transaction_date=transaction_date,
            total=total,
            created_at=now,
        )

    @property
    def is_empty(self) -> bool:
        return self.merchant is None and self.transaction_date is None and self.total is None
