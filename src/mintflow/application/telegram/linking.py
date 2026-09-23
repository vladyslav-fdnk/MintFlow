"""Telegram link challenge and connection records (authentication_persistence_design.md, 2.5-2.6).

Only ``telegram_user_id`` is identity. Display names are presentation metadata
shown back to the initiating Web session and are never used for authorization.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

TELEGRAM_LINK_CHALLENGE_LIFETIME: Final = timedelta(minutes=5)
MAX_TELEGRAM_DISPLAY_NAME_LENGTH: Final = 128


@dataclass(frozen=True, slots=True)
class TelegramLinkChallenge:
    id: UUID
    initiating_user_id: UUID
    initiating_web_session_id: UUID
    issued_at: datetime
    expires_at: datetime
    claimed_at: datetime | None
    claimed_telegram_user_id: int | None
    claimed_telegram_display_name: str | None
    confirmed_at: datetime | None

    @property
    def is_claimed(self) -> bool:
        return self.claimed_at is not None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


@dataclass(frozen=True, slots=True)
class TelegramConnection:
    id: UUID
    user_id: UUID
    telegram_user_id: int
    telegram_display_name: str | None
    linked_at: datetime
    unlinked_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.unlinked_at is None


def telegram_display_name(*, first_name: str, username: str | None) -> str | None:
    """A short, bounded label such as ``Ada (@ada)``, or None when there is nothing to show."""
    parts = [first_name.strip()] if first_name.strip() else []
    if username:
        parts.append(f"(@{username})")
    label = " ".join(parts)
    return label[:MAX_TELEGRAM_DISPLAY_NAME_LENGTH] or None
