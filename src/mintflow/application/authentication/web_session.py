from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from mintflow.application.authentication.tokens import GeneratedToken, generate_token, hash_token

WEB_SESSION_LIFETIME = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class WebSession:
    id: UUID
    user_id: UUID
    secret_hash: bytes
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name, value in (("issued_at", self.issued_at), ("expires_at", self.expires_at)):
            if value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.revoked_at is not None and self.revoked_at.utcoffset() is None:
            raise ValueError("revoked_at must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.expires_at > self.issued_at + WEB_SESSION_LIFETIME:
            raise ValueError("session lifetime must not exceed 30 days")
        if self.revoked_at is not None and self.revoked_at < self.issued_at:
            raise ValueError("revoked_at must not be before issued_at")


@dataclass(frozen=True, slots=True)
class AuthenticatedWebSession:
    session_id: UUID
    user_id: UUID


class WebSessionRepository(Protocol):
    def authenticate(
        self, *, secret_hash: bytes, now: datetime
    ) -> AuthenticatedWebSession | None: ...

    def find_session_id(self, *, secret_hash: bytes) -> UUID | None: ...

    def revoke(self, *, session_id: UUID, revoked_at: datetime) -> bool: ...

    def revoke_all(self, *, user_id: UUID, revoked_at: datetime) -> int: ...


def new_web_session(
    *,
    user_id: UUID,
    issued_at: datetime,
    token_generator: Callable[[], GeneratedToken] = generate_token,
) -> tuple[WebSession, str]:
    if issued_at.utcoffset() is None:
        raise ValueError("issued_at must be timezone-aware")
    issued_at = issued_at.astimezone(UTC)
    token = token_generator()
    return (
        WebSession(
            id=uuid4(),
            user_id=user_id,
            secret_hash=token.digest,
            issued_at=issued_at,
            expires_at=issued_at + WEB_SESSION_LIFETIME,
        ),
        token.raw,
    )


class AuthenticateWebSession:
    def __init__(
        self,
        *,
        repository: WebSessionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        now = _utc_now(self._clock)
        return self._repository.authenticate(secret_hash=hash_token(secret), now=now)


class RevokeWebSession:
    def __init__(
        self,
        *,
        repository: WebSessionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, session_id: UUID) -> bool:
        return self._repository.revoke(session_id=session_id, revoked_at=_utc_now(self._clock))


class RevokeAllWebSessions:
    def __init__(
        self,
        *,
        repository: WebSessionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, user_id: UUID) -> int:
        return self._repository.revoke_all(user_id=user_id, revoked_at=_utc_now(self._clock))


class LogoutWebSession:
    """Revoke the exact session identified by a presented raw secret, if any.

    Unlike ``AuthenticateWebSession``, resolution does not filter by
    ``revoked_at``, ``expires_at``, or User status: logout must still find
    and idempotently revoke a session that is already revoked or expired, so
    the browser cookie can always be cleared without disclosing why. A
    secret matching no session is a silent no-op.
    """

    def __init__(
        self,
        *,
        repository: WebSessionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, secret: str) -> None:
        session_id = self._repository.find_session_id(secret_hash=hash_token(secret))
        if session_id is None:
            return
        self._repository.revoke(session_id=session_id, revoked_at=_utc_now(self._clock))


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now.astimezone(UTC)
