"""Per-user Telegram conversation state (docs/telegram_client_design.md, T5).

Field values live only on the CaptureDraft. The conversation records which
draft is active, what the bot is waiting for, and whether the draft's currency
is still the user's default (the draft cannot carry that provenance itself,
because amount and currency are one Money value).
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AwaitingInput(StrEnum):
    NOTHING = "nothing"
    AMOUNT = "amount"
    CURRENCY = "currency"
    MERCHANT = "merchant"
    CATEGORY = "category"
    DATE = "date"


@dataclass(frozen=True, slots=True)
class TelegramConversation:
    user_id: UUID
    active_draft_id: UUID | None
    awaiting: AwaitingInput
    currency_is_default: bool
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.active_draft_id is None and self.awaiting is not AwaitingInput.NOTHING:
            raise ValueError("a conversation without a draft cannot wait for input")

    @classmethod
    def idle(cls, *, user_id: UUID, now: datetime) -> "TelegramConversation":
        return cls(
            user_id=user_id,
            active_draft_id=None,
            awaiting=AwaitingInput.NOTHING,
            currency_is_default=False,
            updated_at=now,
        )

    def waiting_for(self, awaiting: AwaitingInput, *, now: datetime) -> "TelegramConversation":
        return replace(self, awaiting=awaiting, updated_at=now)

    def with_draft(
        self, draft_id: UUID, *, awaiting: AwaitingInput, currency_is_default: bool, now: datetime
    ) -> "TelegramConversation":
        return replace(
            self,
            active_draft_id=draft_id,
            awaiting=awaiting,
            currency_is_default=currency_is_default,
            updated_at=now,
        )

    def finished(self, *, now: datetime) -> "TelegramConversation":
        return TelegramConversation.idle(user_id=self.user_id, now=now)


class TelegramConversationRepository(Protocol):
    def lock(self, *, user_id: UUID, now: datetime) -> TelegramConversation:
        """Create if missing and lock the user's row for the rest of the transaction."""
        ...

    def save(self, conversation: TelegramConversation) -> None:
        """Insert or replace, without committing."""
        ...
