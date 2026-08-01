from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from mintflow.application.authentication import (
    AuthenticatedUserIdentity,
    ConsumeMagicLink,
    MagicLinkConsumptionResult,
    hash_token,
)
from mintflow.domain.user import User, UserStatus
from mintflow.infrastructure.persistence import SqlAlchemyLoginChallengeConsumer
from mintflow.infrastructure.persistence.models import (
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def add_challenge(
    db_session: Session,
    *,
    token: str,
    canonical_email: str = "person@example.com",
    expires_at: datetime = NOW + timedelta(minutes=15),
) -> None:
    db_session.add(
        LoginChallengeRecord(
            canonical_email=canonical_email,
            token_hash=hash_token(token),
            issued_at=NOW - timedelta(minutes=1),
            expires_at=expires_at,
            consumed_at=None,
            return_target="dashboard",
        )
    )
    db_session.commit()


def consume(session: Session, *, token: str) -> MagicLinkConsumptionResult:
    return ConsumeMagicLink(
        challenge_consumer=SqlAlchemyLoginChallengeConsumer(session),
        clock=lambda: NOW,
    ).execute(token=token)


def test_first_login_consumes_challenge_and_creates_identity(db_session: Session) -> None:
    add_challenge(db_session, token="first-login")

    result = consume(db_session, token="first-login")

    assert result.authenticated
    assert result.identity is not None
    assert result.return_target == "dashboard"
    identity = db_session.scalar(select(EmailIdentityRecord))
    assert identity is not None
    assert identity.user_id == result.identity.user_id
    assert identity.canonical_email == "person@example.com"
    assert identity.display_email == "person@example.com"
    challenge = db_session.scalar(select(LoginChallengeRecord))
    assert challenge is not None
    assert challenge.consumed_at == NOW


def test_reused_challenge_returns_generic_invalid_result(db_session: Session) -> None:
    add_challenge(db_session, token="single-use")
    assert consume(db_session, token="single-use").authenticated

    result = consume(db_session, token="single-use")

    assert not result.authenticated
    assert result.identity is None
    assert result.return_target is None
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 1


def test_unknown_token_returns_generic_invalid_result(db_session: Session) -> None:
    result = consume(db_session, token="unknown")

    assert not result.authenticated
    assert result.identity is None
    assert result.return_target is None
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 0


def test_exact_expiry_boundary_is_invalid_and_unconsumed(db_session: Session) -> None:
    add_challenge(db_session, token="expired", expires_at=NOW)

    result = consume(db_session, token="expired")

    assert not result.authenticated
    challenge = db_session.scalar(select(LoginChallengeRecord))
    assert challenge is not None
    assert challenge.consumed_at is None
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 0


def test_returning_login_resolves_existing_user_without_modifying_identity(
    db_session: Session,
) -> None:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW, deactivated_at=None)
    db_session.add(user)
    db_session.flush()
    identity = EmailIdentityRecord(
        user_id=user.id,
        canonical_email="person@example.com",
        display_email="Person@example.com",
        verified_at=NOW - timedelta(days=1),
        created_at=NOW - timedelta(days=1),
    )
    db_session.add(identity)
    db_session.commit()
    identity_id = identity.id
    add_challenge(db_session, token="returning")

    result = consume(db_session, token="returning")

    assert result.identity == AuthenticatedUserIdentity(user_id=user.id)
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 1
    persisted_identity = db_session.get(EmailIdentityRecord, identity_id)
    assert persisted_identity is not None
    assert persisted_identity.display_email == "Person@example.com"
    assert persisted_identity.verified_at == NOW - timedelta(days=1)


def test_deactivated_user_is_not_authenticated(db_session: Session) -> None:
    user = UserRecord(
        status=UserStatus.DEACTIVATED.value,
        created_at=NOW - timedelta(days=2),
        deactivated_at=NOW - timedelta(days=1),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        EmailIdentityRecord(
            user_id=user.id,
            canonical_email="person@example.com",
            display_email="person@example.com",
            verified_at=NOW - timedelta(days=2),
            created_at=NOW - timedelta(days=2),
        )
    )
    db_session.commit()
    add_challenge(db_session, token="deactivated")

    result = consume(db_session, token="deactivated")

    assert not result.authenticated
    challenge = db_session.scalar(select(LoginChallengeRecord))
    assert challenge is not None
    assert challenge.consumed_at == NOW


def test_first_registration_failure_rolls_back_challenge_and_account(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_challenge(db_session, token="failed-registration")

    def fail_user_creation(*, now: datetime) -> User:
        raise RuntimeError("simulated registration failure")

    monkeypatch.setattr(User, "create", fail_user_creation)

    with pytest.raises(RuntimeError, match="simulated registration failure"):
        consume(db_session, token="failed-registration")

    challenge = db_session.scalar(select(LoginChallengeRecord))
    assert challenge is not None
    assert challenge.consumed_at is None
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 0
    assert db_session.scalar(select(func.count()).select_from(EmailIdentityRecord)) == 0


def test_same_challenge_consumed_concurrently_has_exactly_one_success(
    db_session: Session,
    engine: Engine,
) -> None:
    add_challenge(db_session, token="racing-token")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)

    def worker() -> MagicLinkConsumptionResult:
        with factory() as session:
            barrier.wait()
            return consume(session, token="racing-token")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))

    assert sum(result.authenticated for result in results) == 1
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(EmailIdentityRecord)) == 1


def test_two_challenges_for_unseen_email_create_one_user_and_identity(
    db_session: Session,
    engine: Engine,
) -> None:
    add_challenge(db_session, token="first-token")
    add_challenge(db_session, token="second-token")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(token: str) -> MagicLinkConsumptionResult:
        with factory() as session:
            barrier.wait()
            return consume(session, token=token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ("first-token", "second-token")))

    assert all(result.authenticated for result in results)
    assert results[0].identity == results[1].identity
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(EmailIdentityRecord)) == 1
