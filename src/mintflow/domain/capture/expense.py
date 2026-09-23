from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from mintflow.domain.capture.enums import CaptureSource
from mintflow.domain.capture.money import Money
from mintflow.domain.capture.values import MerchantName, TransactionDate


def _require_aware(now: datetime) -> None:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _require_positive(money: Money) -> None:
    if money.minor_units <= 0:
        raise ValueError("Expense money must be strictly positive")


def _require_stable_key(category_key: str) -> None:
    normalized = category_key.strip().lower()
    if not normalized or normalized != category_key:
        raise ValueError("category key must be a non-empty, stable lowercase identifier")


@dataclass(frozen=True, slots=True)
class Expense:
    """A confirmed financial fact. An Expense is created already confirmed.

    Ownership enforcement (invariant 9) and the cross-aggregate check that
    the Expense, draft, and Receipt owners match (invariant 20) are not
    implemented here: this dataclass has no access to the draft or Receipt,
    so that check is the confirmation use case's (CAPTURE-08) job.

    Post-confirmation edits, deletions, and restorations are recorded by the
    application layer as ``ExpenseChangeRecord`` rows written in the same
    transaction (decision 7; docs/expense_management_design.md, D1). The
    domain methods below only produce the new state.
    """

    id: UUID
    owner_id: UUID
    money: Money
    transaction_date: TransactionDate
    merchant: MerchantName | None
    category_key: str
    note: str | None
    source: CaptureSource
    capture_draft_id: UUID
    receipt_id: UUID | None
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.deleted_at is None

    @classmethod
    def create(
        cls,
        *,
        owner_id: UUID,
        money: Money,
        transaction_date: TransactionDate,
        category_key: str,
        capture_draft_id: UUID,
        merchant: MerchantName | None = None,
        note: str | None = None,
        source: CaptureSource,
        receipt_id: UUID | None = None,
        now: datetime,
    ) -> "Expense":
        _require_aware(now)
        _require_positive(money)
        _require_stable_key(category_key)
        created_at = now.astimezone(UTC)
        return cls(
            id=uuid4(),
            owner_id=owner_id,
            money=money,
            transaction_date=transaction_date,
            merchant=merchant,
            category_key=category_key,
            note=note,
            source=source,
            capture_draft_id=capture_draft_id,
            receipt_id=receipt_id,
            created_at=created_at,
            modified_at=created_at,
            deleted_at=None,
        )

    def edit_money(self, *, money: Money, now: datetime) -> "Expense":
        _require_aware(now)
        _require_positive(money)
        return replace(self, money=money, modified_at=now.astimezone(UTC))

    def edit_merchant(self, *, merchant: MerchantName | None, now: datetime) -> "Expense":
        _require_aware(now)
        return replace(self, merchant=merchant, modified_at=now.astimezone(UTC))

    def edit_category(self, *, category_key: str, now: datetime) -> "Expense":
        _require_aware(now)
        _require_stable_key(category_key)
        return replace(self, category_key=category_key, modified_at=now.astimezone(UTC))

    def edit_note(self, *, note: str | None, now: datetime) -> "Expense":
        _require_aware(now)
        return replace(self, note=note, modified_at=now.astimezone(UTC))

    def edit_transaction_date(
        self, *, transaction_date: TransactionDate, now: datetime
    ) -> "Expense":
        _require_aware(now)
        return replace(self, transaction_date=transaction_date, modified_at=now.astimezone(UTC))

    def delete(self, *, now: datetime) -> "Expense":
        _require_aware(now)
        if self.deleted_at is not None:
            return self
        deleted_at = now.astimezone(UTC)
        return replace(self, deleted_at=deleted_at, modified_at=deleted_at)

    def restore(self, *, now: datetime) -> "Expense":
        _require_aware(now)
        if self.deleted_at is None:
            return self
        return replace(self, deleted_at=None, modified_at=now.astimezone(UTC))
