from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class UserStatus(StrEnum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    status: UserStatus
    created_at: datetime
    deactivated_at: datetime | None

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.deactivated_at is not None and self.deactivated_at.utcoffset() is None:
            raise ValueError("deactivated_at must be timezone-aware")
        if (self.status is UserStatus.ACTIVE) != (self.deactivated_at is None):
            raise ValueError("status and deactivated_at are inconsistent")
        if self.deactivated_at is not None and self.deactivated_at < self.created_at:
            raise ValueError("deactivated_at must not be before created_at")

    @classmethod
    def create(cls, *, now: datetime) -> "User":
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return cls(
            id=uuid4(),
            status=UserStatus.ACTIVE,
            created_at=now.astimezone(UTC),
            deactivated_at=None,
        )

    def deactivate(self, *, now: datetime) -> "User":
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.status is UserStatus.DEACTIVATED:
            return self
        return replace(
            self,
            status=UserStatus.DEACTIVATED,
            deactivated_at=now.astimezone(UTC),
        )
