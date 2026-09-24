"""Delete an account and everything MintFlow keeps about it (docs/account_deletion_design.md).

Deletion is immediate and final (A1). The repository does it in one transaction under a lock on
the user row, clears the user from the authentication audit (A2), records one anonymous
``account_deleted`` event, and keeps a tombstone of the user id for 35 days (A5). Confirming the
user's intent, such as matching the typed email, belongs to the caller.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from mintflow.application.authentication.audit import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
    AuthenticationAuditRecord,
)

# Longer than any backup lives (30 days, operations O7), so a restored account is deleted again.
DELETED_ACCOUNT_RETENTION: Final = timedelta(days=35)


@dataclass(frozen=True, slots=True)
class AccountDeletion:
    user_id: UUID
    deleted_at: datetime
    audit_record: AuthenticationAuditRecord


class AccountDeletionRepository(Protocol):
    def delete_account(self, deletion: AccountDeletion) -> bool:
        """Delete every row owned by the user in one transaction; False if the user is gone."""
        ...


class DeleteAccount:
    def __init__(
        self,
        *,
        repository: AccountDeletionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, *, user_id: UUID) -> bool:
        """True when this call deleted the account; False when it no longer existed."""
        now = self._clock()
        return self._repository.delete_account(
            AccountDeletion(
                user_id=user_id,
                deleted_at=now,
                audit_record=AuthenticationAuditRecord(
                    event_type=AuthenticationAuditEventType.ACCOUNT_DELETED,
                    outcome=AuthenticationAuditOutcome.SUCCEEDED,
                    occurred_at=now,
                    id=self._id_factory(),
                ),
            )
        )
