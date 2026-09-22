from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence.models import EmailIdentityRecord, UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 1, 10, 30, tzinfo=UTC)


def persist_user(db_session: Session, *, status: UserStatus = UserStatus.ACTIVE) -> UserRecord:
    user = UserRecord(
        status=status.value,
        created_at=NOW,
        deactivated_at=NOW + timedelta(days=1) if status is UserStatus.DEACTIVATED else None,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_persists_valid_user_with_timezone_aware_utc_timestamp(db_session: Session) -> None:
    user = persist_user(db_session)

    db_session.refresh(user)

    assert user.id is not None
    assert user.status == UserStatus.ACTIVE.value
    assert user.deactivated_at is None
    assert user.created_at == NOW
    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("status", "deactivated_at"),
    [
        (UserStatus.ACTIVE, NOW),
        (UserStatus.DEACTIVATED, None),
    ],
)
def test_rejects_inconsistent_user_lifecycle(
    db_session: Session,
    status: UserStatus,
    deactivated_at: datetime | None,
) -> None:
    db_session.add(UserRecord(status=status.value, created_at=NOW, deactivated_at=deactivated_at))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_rejects_deactivation_before_creation(db_session: Session) -> None:
    db_session.add(
        UserRecord(
            status=UserStatus.DEACTIVATED.value,
            created_at=NOW,
            deactivated_at=NOW - timedelta(microseconds=1),
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_rejects_unknown_user_status(db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(UserRecord).values(
                id=uuid4(),
                status="suspended",
                created_at=NOW,
                deactivated_at=None,
            )
        )
        db_session.commit()


def test_persists_email_identity_for_user(db_session: Session) -> None:
    user = persist_user(db_session)
    identity = EmailIdentityRecord(
        user_id=user.id,
        canonical_email="person@example.com",
        display_email="Person@example.com",
        verified_at=NOW,
        created_at=NOW,
    )
    db_session.add(identity)
    db_session.commit()

    db_session.refresh(identity)

    assert identity.id is not None
    assert identity.user_id == user.id
    assert identity.verified_at.tzinfo is not None
    assert identity.verified_at.utcoffset() == timedelta(0)


def test_rejects_duplicate_canonical_email(db_session: Session) -> None:
    first_user = persist_user(db_session)
    second_user = persist_user(db_session)
    db_session.add_all(
        [
            EmailIdentityRecord(
                user_id=first_user.id,
                canonical_email="person@example.com",
                display_email="person@example.com",
                verified_at=NOW,
                created_at=NOW,
            ),
            EmailIdentityRecord(
                user_id=second_user.id,
                canonical_email="person@example.com",
                display_email="PERSON@example.com",
                verified_at=NOW,
                created_at=NOW,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_rejects_second_email_identity_for_user(db_session: Session) -> None:
    user = persist_user(db_session)
    for address in ("first@example.com", "second@example.com"):
        db_session.add(
            EmailIdentityRecord(
                user_id=user.id,
                canonical_email=address,
                display_email=address,
                verified_at=NOW,
                created_at=NOW,
            )
        )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_rejects_missing_required_email_field(db_session: Session) -> None:
    user = persist_user(db_session)

    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(EmailIdentityRecord).values(
                id=uuid4(),
                user_id=user.id,
                canonical_email="person@example.com",
                display_email=None,
                verified_at=NOW,
                created_at=NOW,
            )
        )


def test_foreign_key_rejects_unknown_user(db_session: Session) -> None:
    db_session.add(
        EmailIdentityRecord(
            user_id=uuid4(),
            canonical_email="person@example.com",
            display_email="person@example.com",
            verified_at=NOW,
            created_at=NOW,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_user_delete_is_restricted_and_identity_delete_does_not_delete_user(
    db_session: Session,
) -> None:
    user = persist_user(db_session)
    identity = EmailIdentityRecord(
        user_id=user.id,
        canonical_email="person@example.com",
        display_email="person@example.com",
        verified_at=NOW,
        created_at=NOW,
    )
    db_session.add(identity)
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(UserRecord).where(UserRecord.id == user.id))
        db_session.commit()
    db_session.rollback()

    db_session.execute(delete(EmailIdentityRecord).where(EmailIdentityRecord.id == identity.id))
    db_session.commit()

    assert db_session.get(UserRecord, user.id) is not None
