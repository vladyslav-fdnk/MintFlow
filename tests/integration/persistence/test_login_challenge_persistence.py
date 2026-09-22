from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.authentication.login_challenge import LoginChallenge
from mintflow.application.authentication.tokens import hash_token
from mintflow.infrastructure.persistence import SqlAlchemyLoginChallengeStore
from mintflow.infrastructure.persistence.models import LoginChallengeRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def challenge(*, token: str = "token-one") -> LoginChallenge:
    return LoginChallenge(
        id=uuid4(),
        canonical_email="person@example.com",
        token_hash=hash_token(token),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        consumed_at=None,
        return_target="dashboard",
    )


def test_persists_valid_challenge_without_user_dependency(db_session: Session) -> None:
    expected = challenge()

    SqlAlchemyLoginChallengeStore(db_session).add(expected)

    persisted = db_session.scalar(
        select(LoginChallengeRecord).where(LoginChallengeRecord.id == expected.id)
    )
    assert persisted is not None
    assert persisted.canonical_email == expected.canonical_email
    assert persisted.token_hash == expected.token_hash
    assert persisted.issued_at == NOW
    assert persisted.expires_at == NOW + timedelta(minutes=15)
    assert persisted.consumed_at is None
    assert persisted.return_target == "dashboard"
    assert not hasattr(persisted, "raw_token")
    assert not hasattr(persisted, "user_id")


def test_login_challenges_table_has_no_foreign_keys(db_session: Session) -> None:
    bind = db_session.get_bind()

    assert inspect(bind).get_foreign_keys("login_challenges") == []


def test_rejects_duplicate_token_hash(db_session: Session) -> None:
    db_session.add_all(
        [
            LoginChallengeRecord(**record_values(challenge(token="same-token"))),
            LoginChallengeRecord(**record_values(challenge(token="same-token"))),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_allows_multiple_valid_challenges_for_same_email(db_session: Session) -> None:
    store = SqlAlchemyLoginChallengeStore(db_session)
    store.add(challenge(token="older-token"))
    store.add(challenge(token="newer-token"))

    assert db_session.scalar(select(func.count()).select_from(LoginChallengeRecord)) == 2


def test_rejects_token_hash_that_is_not_sha256_length(db_session: Session) -> None:
    values = record_values(challenge())
    values["token_hash"] = b"too-short"

    with pytest.raises(IntegrityError):
        db_session.execute(insert(LoginChallengeRecord).values(**values))
        db_session.commit()


def test_rejects_expiry_not_after_issuance(db_session: Session) -> None:
    expected = challenge()
    values = record_values(expected)
    values["expires_at"] = NOW

    with pytest.raises(IntegrityError):
        db_session.execute(insert(LoginChallengeRecord).values(**values))
        db_session.commit()


def test_rejects_missing_required_field(db_session: Session) -> None:
    values = record_values(challenge())
    values["canonical_email"] = None

    with pytest.raises(IntegrityError):
        db_session.execute(insert(LoginChallengeRecord).values(**values))
        db_session.commit()


def record_values(value: LoginChallenge) -> dict[str, object]:
    return {
        "id": value.id,
        "canonical_email": value.canonical_email,
        "token_hash": value.token_hash,
        "issued_at": value.issued_at,
        "expires_at": value.expires_at,
        "consumed_at": value.consumed_at,
        "return_target": value.return_target,
    }
