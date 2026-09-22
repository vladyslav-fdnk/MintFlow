from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.authentication import (
    AuthenticatedWebSession,
    AuthenticateWebSession,
    LogoutWebSession,
    RevokeAllWebSessions,
    RevokeWebSession,
    hash_token,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import SqlAlchemyWebSessionRepository
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    UserRecord,
    WebSessionRecord,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def add_user(db_session: Session, *, status: UserStatus = UserStatus.ACTIVE) -> UserRecord:
    user = UserRecord(
        status=status.value,
        created_at=NOW - timedelta(days=1),
        deactivated_at=NOW if status is UserStatus.DEACTIVATED else None,
    )
    db_session.add(user)
    db_session.commit()
    return user


def add_session(
    db_session: Session,
    *,
    user_id: UUID,
    secret: str,
    expires_at: datetime = NOW + timedelta(days=30),
) -> WebSessionRecord:
    record = WebSessionRecord(
        user_id=user_id,
        secret_hash=hash_token(secret),
        issued_at=NOW,
        expires_at=expires_at,
        revoked_at=None,
    )
    db_session.add(record)
    db_session.commit()
    return record


def authenticate(
    db_session: Session, *, secret: str, now: datetime = NOW
) -> AuthenticatedWebSession | None:
    return AuthenticateWebSession(
        repository=SqlAlchemyWebSessionRepository(db_session), clock=lambda: now
    ).execute(secret=secret)


def test_authenticates_valid_session_without_persisting_raw_secret(db_session: Session) -> None:
    user = add_user(db_session)
    session = add_session(db_session, user_id=user.id, secret="raw-secret")

    authenticated = authenticate(db_session, secret="raw-secret")

    assert authenticated is not None
    assert authenticated.session_id == session.id
    assert authenticated.user_id == user.id
    persisted = db_session.get(WebSessionRecord, session.id)
    assert persisted is not None
    assert persisted.secret_hash == hash_token("raw-secret")
    assert b"raw-secret" not in persisted.secret_hash


def test_exact_expiry_boundary_and_expired_session_do_not_authenticate(db_session: Session) -> None:
    user = add_user(db_session)
    add_session(db_session, user_id=user.id, secret="boundary", expires_at=NOW + timedelta(hours=1))

    assert authenticate(db_session, secret="boundary", now=NOW + timedelta(hours=1)) is None
    assert authenticate(db_session, secret="boundary", now=NOW + timedelta(hours=2)) is None


def test_current_session_revocation_is_immediate_and_idempotent(db_session: Session) -> None:
    user = add_user(db_session)
    session = add_session(db_session, user_id=user.id, secret="logout")
    repository = SqlAlchemyWebSessionRepository(db_session)

    assert RevokeWebSession(
        repository=repository, clock=lambda: NOW + timedelta(minutes=1)
    ).execute(session_id=session.id)
    assert not RevokeWebSession(
        repository=repository, clock=lambda: NOW + timedelta(minutes=2)
    ).execute(session_id=session.id)
    assert authenticate(db_session, secret="logout") is None


def test_account_wide_revocation_only_revokes_target_users_sessions(db_session: Session) -> None:
    user = add_user(db_session)
    other = add_user(db_session)
    add_session(db_session, user_id=user.id, secret="one")
    add_session(db_session, user_id=user.id, secret="two")
    add_session(db_session, user_id=other.id, secret="other")

    count = RevokeAllWebSessions(
        repository=SqlAlchemyWebSessionRepository(db_session),
        clock=lambda: NOW + timedelta(minutes=1),
    ).execute(user_id=user.id)

    assert count == 2
    assert authenticate(db_session, secret="one") is None
    assert authenticate(db_session, secret="two") is None
    assert authenticate(db_session, secret="other") is not None


def test_multiple_sessions_remain_simultaneously_valid(db_session: Session) -> None:
    user = add_user(db_session)
    add_session(db_session, user_id=user.id, secret="one")
    add_session(db_session, user_id=user.id, secret="two")

    assert authenticate(db_session, secret="one") is not None
    assert authenticate(db_session, secret="two") is not None


def test_deactivated_user_never_authenticates(db_session: Session) -> None:
    user = add_user(db_session, status=UserStatus.DEACTIVATED)
    add_session(db_session, user_id=user.id, secret="inactive")

    assert authenticate(db_session, secret="inactive") is None


def test_database_rejects_invalid_hash_lifetime_and_unknown_user(db_session: Session) -> None:
    user = add_user(db_session)
    for record in (
        WebSessionRecord(
            user_id=user.id, secret_hash=b"short", issued_at=NOW, expires_at=NOW + timedelta(days=1)
        ),
        WebSessionRecord(
            user_id=user.id,
            secret_hash=b"x" * 32,
            issued_at=NOW,
            expires_at=NOW + timedelta(days=31),
        ),
        WebSessionRecord(
            user_id=uuid4(),
            secret_hash=b"y" * 32,
            issued_at=NOW,
            expires_at=NOW + timedelta(days=1),
        ),
    ):
        db_session.add(record)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


def test_logout_finds_and_revokes_a_revoked_or_expired_session(db_session: Session) -> None:
    user = add_user(db_session)
    revoked = add_session(db_session, user_id=user.id, secret="revoked-secret")
    RevokeWebSession(
        repository=SqlAlchemyWebSessionRepository(db_session), clock=lambda: NOW
    ).execute(session_id=revoked.id)
    expired = add_session(
        db_session, user_id=user.id, secret="expired-secret", expires_at=NOW + timedelta(seconds=1)
    )
    repository = SqlAlchemyWebSessionRepository(db_session)

    LogoutWebSession(repository=repository, clock=lambda: NOW + timedelta(minutes=1)).execute(
        secret="expired-secret"
    )

    db_session.expire_all()
    persisted_expired = db_session.get(WebSessionRecord, expired.id)
    assert persisted_expired is not None
    assert persisted_expired.revoked_at == NOW + timedelta(minutes=1)
    persisted_revoked = db_session.get(WebSessionRecord, revoked.id)
    assert persisted_revoked is not None
    assert persisted_revoked.revoked_at == NOW


def test_logout_with_an_unknown_secret_changes_nothing(db_session: Session) -> None:
    user = add_user(db_session)
    session = add_session(db_session, user_id=user.id, secret="kept")
    repository = SqlAlchemyWebSessionRepository(db_session)

    LogoutWebSession(repository=repository, clock=lambda: NOW).execute(secret="never-issued")

    db_session.expire_all()
    persisted = db_session.get(WebSessionRecord, session.id)
    assert persisted is not None
    assert persisted.revoked_at is None
    assert db_session.scalar(select(func.count()).select_from(AuthenticationAuditRecordModel)) == 0


def test_logout_revocation_does_not_partially_commit_when_audit_insertion_fails(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    session = add_session(db_session, user_id=user.id, secret="audit-failure")

    def fail_audit_insert(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated audit insertion failure")

    event.listen(AuthenticationAuditRecordModel, "before_insert", fail_audit_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated audit insertion failure"):
            LogoutWebSession(
                repository=SqlAlchemyWebSessionRepository(db_session), clock=lambda: NOW
            ).execute(secret="audit-failure")
    finally:
        event.remove(AuthenticationAuditRecordModel, "before_insert", fail_audit_insert)
    db_session.rollback()

    persisted = db_session.get(WebSessionRecord, session.id)
    assert persisted is not None
    assert persisted.revoked_at is None
    assert db_session.scalar(select(func.count()).select_from(AuthenticationAuditRecordModel)) == 0


def test_secret_hash_is_unique(db_session: Session) -> None:
    user = add_user(db_session)
    add_session(db_session, user_id=user.id, secret="same")
    db_session.add(
        WebSessionRecord(
            user_id=user.id,
            secret_hash=hash_token("same"),
            issued_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 1
