from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.authentication import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
    AuthenticationAuditRecord,
    ConsumeMagicLink,
    RevokeAllWebSessions,
    RevokeWebSession,
    authentication_audit_cutoff,
    hash_token,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyAuthenticationAuditAppender,
    SqlAlchemyLoginChallengeConsumer,
    SqlAlchemyWebSessionRepository,
)
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
    WebSessionRecord,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def add_user(db_session: Session) -> UserRecord:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    db_session.add(user)
    db_session.commit()
    return user


def test_append_only_adapter_persists_minimal_record_and_nullable_user(
    db_session: Session,
) -> None:
    record = AuthenticationAuditRecord(
        occurred_at=NOW,
        event_type=AuthenticationAuditEventType.LOGIN_FAILED,
        outcome=AuthenticationAuditOutcome.FAILED,
    )

    SqlAlchemyAuthenticationAuditAppender(db_session).append(record)

    persisted = db_session.get(AuthenticationAuditRecordModel, record.id)
    assert persisted is not None
    assert persisted.user_id is None
    assert persisted.subject_record_id is None
    assert set(AuthenticationAuditRecordModel.__table__.columns.keys()) == {
        "id",
        "occurred_at",
        "event_type",
        "outcome",
        "user_id",
        "subject_record_id",
    }
    assert not hasattr(SqlAlchemyAuthenticationAuditAppender, "update")
    assert not hasattr(SqlAlchemyAuthenticationAuditAppender, "delete")


@pytest.mark.parametrize(
    ("column", "value"),
    [("event_type", "arbitrary"), ("outcome", "unknown")],
)
def test_database_rejects_unknown_vocabulary(db_session: Session, column: str, value: str) -> None:
    values = {
        "id": uuid4(),
        "occurred_at": NOW,
        "event_type": AuthenticationAuditEventType.LOGIN_FAILED.value,
        "outcome": AuthenticationAuditOutcome.FAILED.value,
    }
    values[column] = value

    with pytest.raises(IntegrityError):
        db_session.execute(insert(AuthenticationAuditRecordModel).values(**values))
        db_session.commit()


@pytest.mark.parametrize("missing", ["occurred_at", "event_type", "outcome"])
def test_database_rejects_missing_required_fields(db_session: Session, missing: str) -> None:
    values = {
        "id": uuid4(),
        "occurred_at": NOW,
        "event_type": AuthenticationAuditEventType.LOGIN_FAILED.value,
        "outcome": AuthenticationAuditOutcome.FAILED.value,
    }
    del values[missing]

    with pytest.raises(IntegrityError):
        db_session.execute(insert(AuthenticationAuditRecordModel).values(**values))
        db_session.commit()


def test_user_correlation_is_valid_and_restrictive(db_session: Session) -> None:
    user = add_user(db_session)
    SqlAlchemyAuthenticationAuditAppender(db_session).append(
        AuthenticationAuditRecord(
            occurred_at=NOW,
            event_type=AuthenticationAuditEventType.ALL_SESSIONS_REVOKED,
            outcome=AuthenticationAuditOutcome.SUCCEEDED,
            user_id=user.id,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.execute(delete(UserRecord).where(UserRecord.id == user.id))
        db_session.commit()


def test_invalid_user_correlation_is_rejected(db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        SqlAlchemyAuthenticationAuditAppender(db_session).append(
            AuthenticationAuditRecord(
                occurred_at=NOW,
                event_type=AuthenticationAuditEventType.LOGIN_FAILED,
                outcome=AuthenticationAuditOutcome.FAILED,
                user_id=uuid4(),
            )
        )


def test_login_success_is_atomic_and_contains_no_secret(db_session: Session) -> None:
    raw_token = "audit-login-token"
    db_session.add(
        LoginChallengeRecord(
            canonical_email="audit@example.com",
            token_hash=hash_token(raw_token),
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=14),
            return_target="dashboard",
        )
    )
    db_session.commit()

    result = ConsumeMagicLink(
        challenge_consumer=SqlAlchemyLoginChallengeConsumer(db_session), clock=lambda: NOW
    ).execute(token=raw_token)

    audit = db_session.scalar(
        select(AuthenticationAuditRecordModel).where(
            AuthenticationAuditRecordModel.event_type
            == AuthenticationAuditEventType.LOGIN_SUCCEEDED.value
        )
    )
    web_session = db_session.scalar(select(WebSessionRecord))
    assert result.authenticated
    assert audit is not None
    assert web_session is not None
    assert audit.user_id == web_session.user_id
    assert audit.subject_record_id == web_session.id
    persisted_text = " ".join(str(value) for value in vars(audit).values())
    assert raw_token not in persisted_text
    assert result.session_secret is not None
    assert result.session_secret not in persisted_text
    assert "audit@example.com" not in persisted_text


def test_failed_consumption_has_non_disclosing_audit_evidence(db_session: Session) -> None:
    result = ConsumeMagicLink(
        challenge_consumer=SqlAlchemyLoginChallengeConsumer(db_session), clock=lambda: NOW
    ).execute(token="unknown-secret")

    audit = db_session.scalar(select(AuthenticationAuditRecordModel))
    assert not result.authenticated
    assert result.return_target is None
    assert audit is not None
    assert audit.event_type == AuthenticationAuditEventType.LOGIN_FAILED.value
    assert audit.user_id is None
    assert audit.subject_record_id is None
    assert "unknown-secret" not in " ".join(str(value) for value in vars(audit).values())


def test_audit_insertion_failure_rolls_back_complete_first_login(db_session: Session) -> None:
    raw_token = "audit-insert-failure"
    db_session.add(
        LoginChallengeRecord(
            canonical_email="rollback@example.com",
            token_hash=hash_token(raw_token),
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=14),
            return_target="dashboard",
        )
    )
    db_session.commit()

    def fail_audit_insert(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated audit insertion failure")

    event.listen(AuthenticationAuditRecordModel, "before_insert", fail_audit_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated audit insertion failure"):
            ConsumeMagicLink(
                challenge_consumer=SqlAlchemyLoginChallengeConsumer(db_session),
                clock=lambda: NOW,
            ).execute(token=raw_token)
    finally:
        event.remove(AuthenticationAuditRecordModel, "before_insert", fail_audit_insert)
    db_session.rollback()

    challenge = db_session.scalar(select(LoginChallengeRecord))
    assert challenge is not None
    assert challenge.consumed_at is None
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 0
    assert db_session.scalar(select(func.count()).select_from(EmailIdentityRecord)) == 0
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 0
    assert db_session.scalar(select(func.count()).select_from(AuthenticationAuditRecordModel)) == 0


def test_revocation_operations_append_evidence(db_session: Session) -> None:
    user = add_user(db_session)
    first_id = uuid4()
    db_session.add_all(
        [
            WebSessionRecord(
                id=first_id,
                user_id=user.id,
                secret_hash=hash_token("first"),
                issued_at=NOW,
                expires_at=NOW + timedelta(days=30),
            ),
            WebSessionRecord(
                user_id=user.id,
                secret_hash=hash_token("second"),
                issued_at=NOW,
                expires_at=NOW + timedelta(days=30),
            ),
        ]
    )
    db_session.commit()
    repository = SqlAlchemyWebSessionRepository(db_session)

    assert RevokeWebSession(repository=repository, clock=lambda: NOW).execute(session_id=first_id)
    assert (
        RevokeAllWebSessions(repository=repository, clock=lambda: NOW).execute(user_id=user.id) == 1
    )

    records = db_session.scalars(
        select(AuthenticationAuditRecordModel).order_by(AuthenticationAuditRecordModel.event_type)
    ).all()
    assert {record.event_type for record in records} == {
        AuthenticationAuditEventType.CURRENT_SESSION_REVOKED.value,
        AuthenticationAuditEventType.ALL_SESSIONS_REVOKED.value,
    }
    assert all(record.user_id == user.id for record in records)


def test_retention_query_includes_exact_cutoff_only(db_session: Session) -> None:
    cutoff = authentication_audit_cutoff(now=NOW)
    for occurred_at in (
        cutoff - timedelta(microseconds=1),
        cutoff,
        cutoff + timedelta(microseconds=1),
    ):
        SqlAlchemyAuthenticationAuditAppender(db_session).append(
            AuthenticationAuditRecord(
                occurred_at=occurred_at,
                event_type=AuthenticationAuditEventType.LOGIN_FAILED,
                outcome=AuthenticationAuditOutcome.FAILED,
            )
        )

    eligible = db_session.scalar(
        select(func.count())
        .select_from(AuthenticationAuditRecordModel)
        .where(AuthenticationAuditRecordModel.occurred_at <= cutoff)
    )
    assert eligible == 2
