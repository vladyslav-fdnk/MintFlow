"""The Telegram linking ceremony (authentication_design_proposal.md, sections 6-7).

Web issues a five-minute challenge bound to its session; the bot claims it for
the verified sender of a private-chat ``/start <token>``; the same Web session
confirms it. Every failure is one generic outcome that reveals nothing about
other accounts. Raw tokens exist only in memory long enough to build the link.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from mintflow.application.authentication.tokens import GeneratedToken, generate_token, hash_token
from mintflow.application.authentication.web_session import AuthenticatedWebSession
from mintflow.application.telegram.linking import (
    TELEGRAM_LINK_CHALLENGE_LIFETIME,
    TelegramConnection,
    TelegramLinkChallenge,
)
from mintflow.domain.user import User, UserStatus

# What Telegram allows in a /start deep-link payload.
_START_PAYLOAD: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")


class TelegramLinkRepository(Protocol):
    def create_challenge(
        self,
        *,
        token_hash: bytes,
        user_id: UUID,
        web_session_id: UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> TelegramLinkChallenge: ...

    def claim(
        self, *, token_hash: bytes, telegram_user_id: int, display_name: str | None, now: datetime
    ) -> TelegramLinkChallenge | None: ...

    def challenge_for_session(
        self, *, challenge_id: UUID, web_session_id: UUID
    ) -> TelegramLinkChallenge | None: ...

    def confirm(
        self, *, challenge_id: UUID, web_session_id: UUID, now: datetime
    ) -> TelegramConnection | None: ...

    def active_connection_for_user(self, *, user_id: UUID) -> TelegramConnection | None: ...

    def active_connection_for_telegram_user(
        self, *, telegram_user_id: int
    ) -> TelegramConnection | None: ...

    def unlink(self, *, user_id: UUID, now: datetime) -> bool: ...


class UserRepository(Protocol):
    def get(self, user_id: UUID) -> User | None: ...


def _now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class IssuedTelegramLink:
    challenge_id: UUID
    deep_link: str
    expires_at: datetime


class IssueTelegramLinkChallenge:
    def __init__(
        self,
        *,
        repository: TelegramLinkRepository,
        bot_username: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_generator: Callable[[], GeneratedToken] = generate_token,
    ) -> None:
        self._repository = repository
        self._bot_username = bot_username
        self._clock = clock
        self._token_generator = token_generator

    def execute(self, *, session: AuthenticatedWebSession) -> IssuedTelegramLink:
        now = _now(self._clock)
        token = self._token_generator()
        challenge = self._repository.create_challenge(
            token_hash=token.digest,
            user_id=session.user_id,
            web_session_id=session.session_id,
            issued_at=now,
            expires_at=now + TELEGRAM_LINK_CHALLENGE_LIFETIME,
        )
        return IssuedTelegramLink(
            challenge_id=challenge.id,
            deep_link=f"https://t.me/{self._bot_username}?start={token.raw}",
            expires_at=challenge.expires_at,
        )


class ClaimTelegramLink:
    """Claim for the verified sender of a private-chat ``/start <token>``.

    The caller (the bot) must already have verified the update and read the
    sender id from it. Returns only whether the claim succeeded.
    """

    def __init__(
        self,
        *,
        repository: TelegramLinkRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, payload: str, telegram_user_id: int, display_name: str | None) -> bool:
        if not _START_PAYLOAD.fullmatch(payload):
            return False
        claimed = self._repository.claim(
            token_hash=hash_token(payload),
            telegram_user_id=telegram_user_id,
            display_name=display_name,
            now=_now(self._clock),
        )
        return claimed is not None


class TelegramLinkState(StrEnum):
    WAITING_FOR_TELEGRAM = "waiting_for_telegram"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONNECTED = "connected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class TelegramLinkStatus:
    challenge_id: UUID
    state: TelegramLinkState
    expires_at: datetime
    telegram_display_name: str | None


class GetTelegramLinkStatus:
    """The status of a challenge for its initiating session; None for any other session."""

    def __init__(
        self,
        *,
        repository: TelegramLinkRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(
        self, *, challenge_id: UUID, session: AuthenticatedWebSession
    ) -> TelegramLinkStatus | None:
        challenge = self._repository.challenge_for_session(
            challenge_id=challenge_id, web_session_id=session.session_id
        )
        if challenge is None:
            return None
        if challenge.is_confirmed:
            state = TelegramLinkState.CONNECTED
        elif challenge.expires_at <= _now(self._clock):
            state = TelegramLinkState.EXPIRED
        elif challenge.is_claimed:
            state = TelegramLinkState.AWAITING_CONFIRMATION
        else:
            state = TelegramLinkState.WAITING_FOR_TELEGRAM
        return TelegramLinkStatus(
            challenge_id=challenge.id,
            state=state,
            expires_at=challenge.expires_at,
            telegram_display_name=(
                challenge.claimed_telegram_display_name
                if state is not TelegramLinkState.EXPIRED
                else None
            ),
        )


class ConfirmTelegramLink:
    def __init__(
        self,
        *,
        repository: TelegramLinkRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(
        self, *, challenge_id: UUID, session: AuthenticatedWebSession
    ) -> TelegramConnection | None:
        return self._repository.confirm(
            challenge_id=challenge_id,
            web_session_id=session.session_id,
            now=_now(self._clock),
        )


class GetTelegramConnection:
    def __init__(self, *, repository: TelegramLinkRepository) -> None:
        self._repository = repository

    def execute(self, *, user_id: UUID) -> TelegramConnection | None:
        return self._repository.active_connection_for_user(user_id=user_id)


class UnlinkTelegram:
    def __init__(
        self,
        *,
        repository: TelegramLinkRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, user_id: UUID) -> bool:
        return self._repository.unlink(user_id=user_id, now=_now(self._clock))


class ResolveTelegramUser:
    """The active User behind a verified Telegram user id, or None.

    Every bot action goes through this. It requires both an active connection
    and an active User, so a deactivated account can never act from Telegram
    even if its connection row were still present.
    """

    def __init__(
        self, *, repository: TelegramLinkRepository, user_repository: UserRepository
    ) -> None:
        self._repository = repository
        self._user_repository = user_repository

    def execute(self, *, telegram_user_id: int) -> User | None:
        connection = self._repository.active_connection_for_telegram_user(
            telegram_user_id=telegram_user_id
        )
        if connection is None:
            return None
        user = self._user_repository.get(connection.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            return None
        return user
