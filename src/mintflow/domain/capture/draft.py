from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from mintflow.domain.capture.enums import CaptureDraftState, CaptureSource, DraftFieldSource
from mintflow.domain.capture.money import Money
from mintflow.domain.capture.receipt import RecognitionResult
from mintflow.domain.capture.values import MerchantName, TransactionDate

# States from which the draft may still be edited, cancelled, or expired. A
# receipt draft is editable while recognition runs: the user's input always wins.
_OPEN_STATES = (
    CaptureDraftState.COLLECTING,
    CaptureDraftState.AWAITING_RECOGNITION,
    CaptureDraftState.READY_FOR_REVIEW,
)
# Fields a recognition result may fill: never one the user has supplied.
_RECOGNITION_MAY_FILL = (None, DraftFieldSource.DEFAULT)


def _require_aware(now: datetime) -> None:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _reject_recognition_provenance(source: DraftFieldSource) -> None:
    if source is DraftFieldSource.RECOGNITION:
        raise ValueError("recognition provenance is set only by apply_recognition")


def _require_stable_key(category_key: str) -> None:
    normalized = category_key.strip().lower()
    if not normalized or normalized != category_key:
        raise ValueError("category key must be a non-empty, stable lowercase identifier")


@dataclass(frozen=True, slots=True)
class CaptureDraft:
    """Mutable, unconfirmed expense information (manual capture only this sprint).

    Every transition returns a new frozen instance. Confirming does not
    create the resulting Expense itself (the confirmation use case owns
    that); this method only records that confirmation happened exactly
    once and which Expense it produced.
    """

    id: UUID
    owner_id: UUID
    source: CaptureSource
    state: CaptureDraftState
    revision: int
    amount: Money | None
    amount_source: DraftFieldSource | None
    transaction_date: TransactionDate | None
    transaction_date_source: DraftFieldSource | None
    merchant: MerchantName | None
    merchant_source: DraftFieldSource | None
    category_key: str | None
    category_key_source: DraftFieldSource | None
    note: str | None
    expense_id: UUID | None
    created_at: datetime
    modified_at: datetime
    confirmed_at: datetime | None
    receipt_id: UUID | None = None
    recognition_result_id: UUID | None = None

    def _ensure_owner(self, caller_id: UUID) -> None:
        if caller_id != self.owner_id:
            raise ValueError("caller is not the owner of this draft")

    def _ensure_editable(self) -> None:
        if self.state not in _OPEN_STATES:
            raise ValueError(f"draft cannot be edited from state {self.state.value!r}")

    @classmethod
    def start(cls, *, owner_id: UUID, source: CaptureSource, now: datetime) -> "CaptureDraft":
        _require_aware(now)
        created_at = now.astimezone(UTC)
        return cls(
            id=uuid4(),
            owner_id=owner_id,
            source=source,
            state=CaptureDraftState.COLLECTING,
            revision=0,
            amount=None,
            amount_source=None,
            transaction_date=None,
            transaction_date_source=None,
            merchant=None,
            merchant_source=None,
            category_key=None,
            category_key_source=None,
            note=None,
            expense_id=None,
            created_at=created_at,
            modified_at=created_at,
            confirmed_at=None,
        )

    @classmethod
    def start_from_receipt(
        cls, *, owner_id: UUID, receipt_id: UUID, now: datetime
    ) -> "CaptureDraft":
        """A Telegram receipt draft, waiting for recognition (receipt_recognition_design R5)."""
        draft = cls.start(owner_id=owner_id, source=CaptureSource.TELEGRAM_RECEIPT, now=now)
        return replace(draft, state=CaptureDraftState.AWAITING_RECOGNITION, receipt_id=receipt_id)

    def apply_recognition(
        self, *, result: RecognitionResult, fallback_category_key: str, now: datetime
    ) -> "CaptureDraft":
        """Fill only empty or defaulted fields from a result; user-supplied values always win.

        Proposes ``fallback_category_key`` (Uncategorized) when no category is set. A draft
        still waiting for recognition moves to review when amount and date are known, and to
        collecting otherwise, so the user is asked for what is missing.
        """
        _require_aware(now)
        if result.owner_id != self.owner_id or result.receipt_id != self.receipt_id:
            raise ValueError("result belongs to another draft")
        self._ensure_editable()
        _require_stable_key(fallback_category_key)
        draft = self
        recognized = DraftFieldSource.RECOGNITION
        if result.total is not None and draft.amount_source in _RECOGNITION_MAY_FILL:
            draft = replace(draft, amount=result.total, amount_source=recognized)
        if (
            result.transaction_date is not None
            and draft.transaction_date_source in _RECOGNITION_MAY_FILL
        ):
            draft = replace(
                draft, transaction_date=result.transaction_date, transaction_date_source=recognized
            )
        if result.merchant is not None and draft.merchant_source in _RECOGNITION_MAY_FILL:
            draft = replace(draft, merchant=result.merchant, merchant_source=recognized)
        if draft.category_key is None:
            draft = replace(
                draft,
                category_key=fallback_category_key,
                category_key_source=DraftFieldSource.DEFAULT,
            )
        state = draft.state
        if state is CaptureDraftState.AWAITING_RECOGNITION:
            complete = draft.amount is not None and draft.transaction_date is not None
            state = CaptureDraftState.READY_FOR_REVIEW if complete else CaptureDraftState.COLLECTING
        return replace(
            draft,
            state=state,
            recognition_result_id=result.id,
            revision=self.revision + 1,
            modified_at=now.astimezone(UTC),
        )

    def continue_manually(self, *, caller_id: UUID, now: datetime) -> "CaptureDraft":
        """Stop waiting for recognition; a late result may still fill untouched fields."""
        self._ensure_owner(caller_id)
        _require_aware(now)
        if self.state is not CaptureDraftState.AWAITING_RECOGNITION:
            return self
        return replace(self, state=CaptureDraftState.COLLECTING, modified_at=now.astimezone(UTC))

    def set_amount(
        self,
        *,
        caller_id: UUID,
        amount: Money,
        now: datetime,
        source: DraftFieldSource = DraftFieldSource.USER,
    ) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        self._ensure_editable()
        _require_aware(now)
        _reject_recognition_provenance(source)
        return replace(
            self,
            amount=amount,
            amount_source=source,
            revision=self.revision + 1,
            modified_at=now.astimezone(UTC),
        )

    def set_transaction_date(
        self,
        *,
        caller_id: UUID,
        transaction_date: TransactionDate,
        now: datetime,
        source: DraftFieldSource = DraftFieldSource.USER,
    ) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        self._ensure_editable()
        _require_aware(now)
        _reject_recognition_provenance(source)
        return replace(
            self,
            transaction_date=transaction_date,
            transaction_date_source=source,
            revision=self.revision + 1,
            modified_at=now.astimezone(UTC),
        )

    def set_merchant(
        self,
        *,
        caller_id: UUID,
        merchant: MerchantName,
        now: datetime,
        source: DraftFieldSource = DraftFieldSource.USER,
    ) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        self._ensure_editable()
        _require_aware(now)
        _reject_recognition_provenance(source)
        return replace(
            self,
            merchant=merchant,
            merchant_source=source,
            revision=self.revision + 1,
            modified_at=now.astimezone(UTC),
        )

    def set_category_key(
        self,
        *,
        caller_id: UUID,
        category_key: str,
        now: datetime,
        source: DraftFieldSource = DraftFieldSource.USER,
    ) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        self._ensure_editable()
        _require_aware(now)
        _reject_recognition_provenance(source)
        _require_stable_key(category_key)
        return replace(
            self,
            category_key=category_key,
            category_key_source=source,
            revision=self.revision + 1,
            modified_at=now.astimezone(UTC),
        )

    def set_note(self, *, caller_id: UUID, note: str | None, now: datetime) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        self._ensure_editable()
        _require_aware(now)
        return replace(
            self,
            note=note,
            revision=self.revision + 1,
            modified_at=now.astimezone(UTC),
        )

    def mark_ready_for_review(self, *, caller_id: UUID, now: datetime) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        if self.state != CaptureDraftState.COLLECTING:
            raise ValueError(f"cannot mark ready for review from state {self.state.value!r}")
        _require_aware(now)
        return replace(
            self,
            state=CaptureDraftState.READY_FOR_REVIEW,
            modified_at=now.astimezone(UTC),
        )

    def is_confirmable(self) -> bool:
        return (
            self.state == CaptureDraftState.READY_FOR_REVIEW
            and self.amount is not None
            and self.transaction_date is not None
        )

    def confirm(self, *, caller_id: UUID, expense_id: UUID, now: datetime) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        if not self.is_confirmable():
            raise ValueError("draft is not confirmable")
        _require_aware(now)
        confirmed_at = now.astimezone(UTC)
        return replace(
            self,
            state=CaptureDraftState.CONFIRMED,
            expense_id=expense_id,
            confirmed_at=confirmed_at,
            modified_at=confirmed_at,
        )

    def cancel(self, *, caller_id: UUID, now: datetime) -> "CaptureDraft":
        self._ensure_owner(caller_id)
        if self.state not in _OPEN_STATES:
            raise ValueError(f"cannot cancel from state {self.state.value!r}")
        _require_aware(now)
        return replace(self, state=CaptureDraftState.CANCELLED, modified_at=now.astimezone(UTC))

    def expire(self, *, now: datetime) -> "CaptureDraft":
        """Expire an abandoned draft.

        No owner check: unlike edit/cancel/confirm (invariant 8), expiration
        is a system-initiated transition, not a user action. Deciding when
        to call this is a later application concern (out of scope here).
        """
        if self.state not in _OPEN_STATES:
            raise ValueError(f"cannot expire from state {self.state.value!r}")
        _require_aware(now)
        return replace(self, state=CaptureDraftState.EXPIRED, modified_at=now.astimezone(UTC))
