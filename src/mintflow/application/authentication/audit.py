from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

AUTHENTICATION_AUDIT_RETENTION = timedelta(days=90)


class AuthenticationAuditEventType(StrEnum):
    LOGIN_CHALLENGE_REQUESTED = "login_challenge_requested"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    CURRENT_SESSION_REVOKED = "current_session_revoked"
    ALL_SESSIONS_REVOKED = "all_sessions_revoked"
    TELEGRAM_LINK_CLAIMED = "telegram_link_claimed"
    TELEGRAM_LINKED = "telegram_linked"
    TELEGRAM_UNLINKED = "telegram_unlinked"
    # Anonymous: recorded without a user id once the account is gone (account deletion, A2).
    ACCOUNT_DELETED = "account_deleted"


class AuthenticationAuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AuthenticationAuditRecord:
    event_type: AuthenticationAuditEventType
    outcome: AuthenticationAuditOutcome
    occurred_at: datetime
    user_id: UUID | None = None
    subject_record_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))


class AuthenticationAuditAppender(Protocol):
    def append(self, record: AuthenticationAuditRecord) -> None: ...


def authentication_audit_cutoff(*, now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC) - AUTHENTICATION_AUDIT_RETENTION


def is_authentication_audit_record_eligible(*, occurred_at: datetime, cutoff: datetime) -> bool:
    if occurred_at.utcoffset() is None or cutoff.utcoffset() is None:
        raise ValueError("occurred_at and cutoff must be timezone-aware")
    return occurred_at.astimezone(UTC) <= cutoff.astimezone(UTC)
