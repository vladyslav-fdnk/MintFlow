from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mintflow.application.authentication import (
    WEB_SESSION_LIFETIME,
    AuthenticatedWebSession,
    AuthenticateWebSession,
    LogoutWebSession,
    RevokeAllWebSessions,
    RevokeWebSession,
    hash_token,
    new_web_session,
)
from mintflow.application.authentication.tokens import GeneratedToken

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class RecordingRepository:
    def __init__(self, *, found_session_id: UUID | None = None) -> None:
        self.authenticated = AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4())
        self.secret_hash: bytes | None = None
        self.now: datetime | None = None
        self.revoked_session_id: UUID | None = None
        self.revoked_user_id: UUID | None = None
        self.revoked_at: datetime | None = None
        self.found_session_id = found_session_id
        self.looked_up_hash: bytes | None = None

    def authenticate(self, *, secret_hash: bytes, now: datetime) -> AuthenticatedWebSession | None:
        self.secret_hash = secret_hash
        self.now = now
        return self.authenticated

    def find_session_id(self, *, secret_hash: bytes) -> UUID | None:
        self.looked_up_hash = secret_hash
        return self.found_session_id

    def revoke(self, *, session_id: UUID, revoked_at: datetime) -> bool:
        self.revoked_session_id = session_id
        self.revoked_at = revoked_at
        return True

    def revoke_all(self, *, user_id: UUID, revoked_at: datetime) -> int:
        self.revoked_user_id = user_id
        self.revoked_at = revoked_at
        return 2


def test_creates_hash_only_session_with_absolute_30_day_expiry() -> None:
    token = GeneratedToken(raw="raw-session-secret", digest=hash_token("raw-session-secret"))
    user_id = uuid4()

    session, raw = new_web_session(user_id=user_id, issued_at=NOW, token_generator=lambda: token)

    assert raw == "raw-session-secret"
    assert session.user_id == user_id
    assert session.secret_hash == token.digest
    assert session.expires_at == NOW + WEB_SESSION_LIFETIME
    assert session.revoked_at is None
    assert not hasattr(session, "raw")


def test_authentication_hashes_secret_and_uses_clock() -> None:
    repository = RecordingRepository()

    result = AuthenticateWebSession(repository=repository, clock=lambda: NOW).execute(secret="raw")

    assert result == repository.authenticated
    assert repository.secret_hash == hash_token("raw")
    assert repository.now == NOW


def test_revocation_use_cases_use_same_utc_clock_boundary() -> None:
    repository = RecordingRepository()
    session_id = uuid4()
    user_id = uuid4()

    assert RevokeWebSession(repository=repository, clock=lambda: NOW).execute(session_id=session_id)
    assert repository.revoked_session_id == session_id
    assert repository.revoked_at == NOW
    assert (
        RevokeAllWebSessions(repository=repository, clock=lambda: NOW).execute(user_id=user_id) == 2
    )
    assert repository.revoked_user_id == user_id


def test_rejects_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AuthenticateWebSession(
            repository=RecordingRepository(), clock=lambda: NOW.replace(tzinfo=None)
        ).execute(secret="raw")


def test_logout_finds_by_hash_and_revokes_that_exact_session() -> None:
    session_id = uuid4()
    repository = RecordingRepository(found_session_id=session_id)

    LogoutWebSession(repository=repository, clock=lambda: NOW).execute(secret="raw")

    assert repository.looked_up_hash == hash_token("raw")
    assert repository.revoked_session_id == session_id
    assert repository.revoked_at == NOW


def test_logout_is_a_silent_no_op_when_no_session_matches_the_secret() -> None:
    repository = RecordingRepository(found_session_id=None)

    LogoutWebSession(repository=repository, clock=lambda: NOW).execute(secret="unknown")

    assert repository.looked_up_hash == hash_token("unknown")
    assert repository.revoked_session_id is None


def test_rejects_session_lifetime_over_30_days() -> None:
    from mintflow.application.authentication.web_session import WebSession

    with pytest.raises(ValueError, match="must not exceed"):
        WebSession(
            id=uuid4(),
            user_id=uuid4(),
            secret_hash=b"x" * 32,
            issued_at=NOW,
            expires_at=NOW + timedelta(days=30, microseconds=1),
        )
