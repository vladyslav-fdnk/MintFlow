from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from mintflow.application.authentication import (
    AuthenticationRetentionResult,
    CleanUpAuthenticationRetention,
    hash_token,
)
from mintflow.application.authentication.audit import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import PostgreSQLAuthenticationRetentionRepository
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    AuthenticationRateLimitBucketRecord,
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
    WebSessionRecord,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def add_user_and_identity(session: Session) -> UserRecord:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=120))
    session.add(user)
    session.flush()
    session.add(
        EmailIdentityRecord(
            user_id=user.id,
            canonical_email=f"{user.id}@example.test",
            display_email=f"{user.id}@example.test",
            verified_at=NOW - timedelta(days=120),
            created_at=NOW - timedelta(days=120),
        )
    )
    session.commit()
    return user


def add_challenge(
    session: Session, *, marker: str, expires_at: datetime, consumed_at: datetime | None
) -> LoginChallengeRecord:
    record = LoginChallengeRecord(
        canonical_email=f"{marker}@example.test",
        token_hash=hash_token(marker),
        issued_at=expires_at - timedelta(minutes=15),
        expires_at=expires_at,
        consumed_at=consumed_at,
        return_target="dashboard",
    )
    session.add(record)
    return record


def add_web_session(
    session: Session,
    *,
    user_id: UUID,
    marker: str,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    issued_at: datetime | None = None,
) -> WebSessionRecord:
    record = WebSessionRecord(
        user_id=user_id,
        secret_hash=hash_token(marker),
        issued_at=issued_at or expires_at - timedelta(days=30),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    session.add(record)
    return record


def cleanup(
    session: Session,
    *,
    batch_size: int = 100,
) -> AuthenticationRetentionResult:
    return CleanUpAuthenticationRetention(
        repository=PostgreSQLAuthenticationRetentionRepository(session), clock=lambda: NOW
    ).execute(batch_size=batch_size)


def test_exact_boundaries_and_ineligible_records_across_all_retained_data(
    db_session: Session,
) -> None:
    boundary_30 = NOW - timedelta(days=30)
    user = add_user_and_identity(db_session)
    consumed_old = add_challenge(
        db_session,
        marker="consumed-old",
        expires_at=NOW - timedelta(days=1),
        consumed_at=boundary_30 - timedelta(microseconds=1),
    )
    consumed_exact = add_challenge(
        db_session,
        marker="consumed-exact",
        expires_at=NOW - timedelta(days=1),
        consumed_at=boundary_30,
    )
    consumed_live = add_challenge(
        db_session,
        marker="consumed-live",
        expires_at=NOW + timedelta(days=1),
        consumed_at=boundary_30 + timedelta(microseconds=1),
    )
    expired_old = add_challenge(
        db_session,
        marker="expired-old",
        expires_at=boundary_30 - timedelta(microseconds=1),
        consumed_at=None,
    )
    expired_exact = add_challenge(
        db_session, marker="expired-exact", expires_at=boundary_30, consumed_at=None
    )
    expired_live = add_challenge(
        db_session,
        marker="expired-live",
        expires_at=boundary_30 + timedelta(microseconds=1),
        consumed_at=None,
    )
    revoked_exact = add_web_session(
        db_session,
        user_id=user.id,
        marker="revoked-exact",
        expires_at=NOW,
        revoked_at=boundary_30,
    )
    revoked_live = add_web_session(
        db_session,
        user_id=user.id,
        marker="revoked-live",
        expires_at=NOW,
        revoked_at=boundary_30 + timedelta(microseconds=1),
    )
    expired_session = add_web_session(
        db_session,
        user_id=user.id,
        marker="session-expired-exact",
        expires_at=boundary_30,
    )
    active_session = add_web_session(
        db_session, user_id=user.id, marker="session-active", expires_at=NOW + timedelta(days=1)
    )
    db_session.add_all(
        [
            AuthenticationRateLimitBucketRecord(
                dimension="email_delivery",
                key_digest=b"a" * 32,
                window_started_at=NOW - timedelta(minutes=15),
                count=1,
                expires_at=NOW,
            ),
            AuthenticationRateLimitBucketRecord(
                dimension="email_delivery",
                key_digest=b"b" * 32,
                window_started_at=NOW - timedelta(minutes=15),
                count=1,
                expires_at=NOW + timedelta(microseconds=1),
            ),
            AuthenticationAuditRecordModel(
                occurred_at=NOW - timedelta(days=90),
                event_type=AuthenticationAuditEventType.LOGIN_FAILED.value,
                outcome=AuthenticationAuditOutcome.FAILED.value,
                user_id=user.id,
            ),
            AuthenticationAuditRecordModel(
                occurred_at=NOW - timedelta(days=90) + timedelta(microseconds=1),
                event_type=AuthenticationAuditEventType.LOGIN_FAILED.value,
                outcome=AuthenticationAuditOutcome.FAILED.value,
                user_id=user.id,
            ),
        ]
    )
    db_session.commit()

    result = cleanup(db_session)

    assert result.login_challenges_deleted == 4
    assert result.web_sessions_deleted == 2
    assert result.rate_limit_buckets_deleted == 1
    assert result.authentication_audit_records_deleted == 1
    remaining_challenges = set(db_session.scalars(select(LoginChallengeRecord.id)))
    assert remaining_challenges == {consumed_live.id, expired_live.id}
    assert consumed_old.id not in remaining_challenges
    assert consumed_exact.id not in remaining_challenges
    assert expired_old.id not in remaining_challenges
    assert expired_exact.id not in remaining_challenges
    assert set(db_session.scalars(select(WebSessionRecord.id))) == {
        revoked_live.id,
        active_session.id,
    }
    assert revoked_exact.id != expired_session.id
    assert db_session.get(UserRecord, user.id) is not None
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EmailIdentityRecord)
            .where(EmailIdentityRecord.user_id == user.id)
        )
        == 1
    )


def test_batches_are_bounded_across_invocations_and_repeated_execution_is_idempotent(
    db_session: Session,
) -> None:
    for number in range(5):
        add_challenge(
            db_session,
            marker=f"batch-{number}",
            expires_at=NOW - timedelta(days=31),
            consumed_at=None,
        )
    db_session.commit()

    counts = [cleanup(db_session, batch_size=2).login_challenges_deleted for _ in range(4)]

    assert counts == [2, 2, 1, 0]


def test_concurrent_cleanup_uses_separate_connections_without_overreporting(engine: Engine) -> None:
    with Session(engine) as setup:
        for number in range(20):
            add_challenge(
                setup,
                marker=f"concurrent-{number}",
                expires_at=NOW - timedelta(days=31),
                consumed_at=None,
            )
        setup.commit()
    barrier = Barrier(2)

    def run_cleanup() -> int:
        with Session(engine) as session:
            service = CleanUpAuthenticationRetention(
                repository=PostgreSQLAuthenticationRetentionRepository(session), clock=lambda: NOW
            )
            barrier.wait()
            return service.execute(batch_size=20).login_challenges_deleted

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: run_cleanup(), range(2)))

    assert sum(counts) == 20
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LoginChallengeRecord)) == 0


def test_failed_delete_transaction_rolls_back_and_returns_no_result(db_session: Session) -> None:
    add_challenge(
        db_session,
        marker="rollback-cleanup",
        expires_at=NOW - timedelta(days=31),
        consumed_at=None,
    )
    db_session.commit()
    db_session.execute(
        text(
            "CREATE FUNCTION pg_temp.reject_auth_cleanup() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'cleanup rejected'; END $$"
        )
    )
    db_session.execute(
        text(
            "CREATE TRIGGER reject_auth_cleanup BEFORE DELETE ON login_challenges "
            "FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_auth_cleanup()"
        )
    )
    db_session.commit()

    with pytest.raises(DatabaseError, match="cleanup rejected"):
        cleanup(db_session)
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(LoginChallengeRecord)) == 1
    db_session.execute(text("DROP TRIGGER reject_auth_cleanup ON login_challenges"))
    db_session.commit()
