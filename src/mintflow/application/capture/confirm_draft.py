from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from mintflow.application.capture.transaction_dates import is_within_future_tolerance
from mintflow.domain.capture import UNCATEGORIZED_KEY, CaptureDraft, CaptureDraftState, Expense
from mintflow.domain.user import User


class CaptureDraftAccessDenied(Exception):
    """The draft does not exist or is not owned by the caller.

    Deliberately uniform: the caller cannot distinguish "no such draft"
    from "a draft owned by someone else exists", matching the pattern
    established by AUTH-12's generic unauthenticated response.
    """


class CaptureDraftNotConfirmable(Exception):
    """The draft cannot be confirmed in its current state."""


class CaptureDraftRepository(Protocol):
    def get_for_update(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None: ...

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None: ...


class ExpenseRepository(Protocol):
    def create(self, expense: Expense, *, commit: bool = True) -> None: ...

    def get(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None: ...


class UserRepository(Protocol):
    def get(self, user_id: UUID) -> User | None: ...


class ConfirmCaptureDraft:
    """Convert one CaptureDraft into exactly one Expense, atomically and idempotently.

    Mirrors the reservation-authority pattern from `ludora`'s ADR-001 and
    `complete_payment`: lock the aggregate first, verify authority/ownership,
    detect an already-completed outcome before doing any work, and make the
    terminal transition and its side effect atomic in one transaction.
    """

    def __init__(
        self,
        *,
        draft_repository: CaptureDraftRepository,
        expense_repository: ExpenseRepository,
        user_repository: UserRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._draft_repository = draft_repository
        self._expense_repository = expense_repository
        self._user_repository = user_repository
        self._clock = clock

    def execute(self, *, draft_id: UUID, caller_id: UUID) -> Expense:
        draft = self._draft_repository.get_for_update(draft_id=draft_id, owner_id=caller_id)
        if draft is None:
            raise CaptureDraftAccessDenied("draft not found or not owned by caller")

        if draft.state is CaptureDraftState.CONFIRMED:
            return self._existing_expense_for(draft, caller_id=caller_id)

        if not draft.is_confirmable():
            raise CaptureDraftNotConfirmable("draft is not confirmable")

        amount = draft.amount
        transaction_date = draft.transaction_date
        if amount is None or transaction_date is None:
            raise CaptureDraftNotConfirmable("draft is missing a mandatory field")

        now = self._clock()
        user = self._user_repository.get(caller_id)
        if user is None:
            raise CaptureDraftAccessDenied("owner not found")
        if not is_within_future_tolerance(transaction_date, now=now, timezone=user.timezone):
            raise CaptureDraftNotConfirmable("transaction date is more than one day in the future")

        category_key = draft.category_key if draft.category_key is not None else UNCATEGORIZED_KEY
        expense = Expense.create(
            owner_id=draft.owner_id,
            money=amount,
            transaction_date=transaction_date,
            category_key=category_key,
            capture_draft_id=draft.id,
            merchant=draft.merchant,
            note=draft.note,
            source=draft.source,
            receipt_id=draft.receipt_id,
            now=now,
        )
        self._expense_repository.create(expense, commit=False)
        confirmed_draft = draft.confirm(caller_id=caller_id, expense_id=expense.id, now=now)
        self._draft_repository.update(confirmed_draft, commit=True)
        return expense

    def _existing_expense_for(self, draft: CaptureDraft, *, caller_id: UUID) -> Expense:
        if draft.expense_id is None:
            raise CaptureDraftNotConfirmable("confirmed draft is missing its resulting expense id")
        existing = self._expense_repository.get(expense_id=draft.expense_id, owner_id=caller_id)
        if existing is None:
            raise CaptureDraftNotConfirmable("confirmed draft has no linked expense")
        if not existing.is_active:
            # Returning it would present a soft-deleted Expense as financial
            # history (EXPENSE-07); restoring it is the way back.
            raise CaptureDraftNotConfirmable("confirmed draft's expense has been deleted")
        return existing
