from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mintflow.domain.user import User, UserStatus

CREATED_AT = datetime(2026, 8, 1, 10, 30, tzinfo=UTC)


def test_creates_user_with_timezone_aware_datetime() -> None:
    user = User.create(now=CREATED_AT)

    assert user.status is UserStatus.ACTIVE
    assert user.created_at == CREATED_AT
    assert user.deactivated_at is None


def test_rejects_naive_datetime_during_creation() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        User.create(now=datetime(2026, 8, 1, 10, 30))


def test_deactivates_user_at_timezone_aware_datetime() -> None:
    user = User.create(now=CREATED_AT)
    deactivated_at = CREATED_AT + timedelta(days=1)

    deactivated_user = user.deactivate(now=deactivated_at)

    assert deactivated_user.status is UserStatus.DEACTIVATED
    assert deactivated_user.deactivated_at == deactivated_at


def test_rejects_naive_datetime_during_deactivation() -> None:
    user = User.create(now=CREATED_AT)

    with pytest.raises(ValueError, match="now must be timezone-aware"):
        user.deactivate(now=datetime(2026, 8, 2, 10, 30))


def test_rejects_deactivation_before_creation() -> None:
    user = User.create(now=CREATED_AT)

    with pytest.raises(ValueError, match="deactivated_at must not be before created_at"):
        user.deactivate(now=CREATED_AT - timedelta(microseconds=1))


def test_repeated_deactivation_is_idempotent() -> None:
    user = User.create(now=CREATED_AT).deactivate(now=CREATED_AT + timedelta(days=1))

    assert user.deactivate(now=CREATED_AT + timedelta(days=2)) is user


@pytest.mark.parametrize(
    ("status", "deactivated_at"),
    [
        (UserStatus.ACTIVE, CREATED_AT),
        (UserStatus.DEACTIVATED, None),
    ],
)
def test_rejects_inconsistent_status_and_deactivated_at(
    status: UserStatus,
    deactivated_at: datetime | None,
) -> None:
    with pytest.raises(ValueError, match="status and deactivated_at are inconsistent"):
        User(
            id=uuid4(),
            status=status,
            created_at=CREATED_AT,
            deactivated_at=deactivated_at,
        )
