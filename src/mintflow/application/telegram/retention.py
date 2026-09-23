"""Telegram retention and abandoned-draft expiry (docs/telegram_client_design.md, T9).

One batch per data kind per run, like the authentication cleanup (AUTH-15); a
scheduler reruns it. Expiring a draft never creates an Expense. Receipt images and
recognition results go 30 days after their draft finished; Expenses and their
receipt ids are never touched (receipt_recognition_design.md, R4).
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from mintflow.application.authentication.retention import (
    validate_authentication_retention_batch_size,
)

PROCESSED_UPDATE_RETENTION: Final = timedelta(days=7)
TELEGRAM_LINK_CHALLENGE_RETENTION: Final = timedelta(days=30)
UNLINKED_TELEGRAM_CONNECTION_RETENTION: Final = timedelta(days=30)
ABANDONED_DRAFT_INACTIVITY: Final = timedelta(days=7)
# Receipt images and recognition results, after their draft finished (design R4).
RECEIPT_DATA_RETENTION: Final = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class TelegramRetentionResult:
    processed_updates_deleted: int
    link_challenges_deleted: int
    unlinked_connections_deleted: int
    drafts_expired: int
    receipts_cleared: int

    def aggregate_counts(self) -> dict[str, int]:
        counts = asdict(self)
        return {**counts, "total": sum(counts.values())}


class TelegramRetentionRepository(Protocol):
    def delete_processed_updates(self, *, processed_cutoff: datetime, batch_size: int) -> int: ...

    def delete_link_challenges(self, *, finished_cutoff: datetime, batch_size: int) -> int: ...

    def delete_unlinked_connections(self, *, unlinked_cutoff: datetime, batch_size: int) -> int: ...

    def expire_abandoned_drafts(
        self, *, inactive_cutoff: datetime, now: datetime, batch_size: int
    ) -> int: ...

    def delete_receipt_data(
        self, *, finished_cutoff: datetime, now: datetime, batch_size: int
    ) -> int: ...


class CleanUpTelegramRetention:
    def __init__(
        self,
        *,
        repository: TelegramRetentionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, batch_size: int) -> TelegramRetentionResult:
        size = validate_authentication_retention_batch_size(batch_size)
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        return TelegramRetentionResult(
            processed_updates_deleted=self._repository.delete_processed_updates(
                processed_cutoff=now - PROCESSED_UPDATE_RETENTION, batch_size=size
            ),
            link_challenges_deleted=self._repository.delete_link_challenges(
                finished_cutoff=now - TELEGRAM_LINK_CHALLENGE_RETENTION, batch_size=size
            ),
            unlinked_connections_deleted=self._repository.delete_unlinked_connections(
                unlinked_cutoff=now - UNLINKED_TELEGRAM_CONNECTION_RETENTION, batch_size=size
            ),
            drafts_expired=self._repository.expire_abandoned_drafts(
                inactive_cutoff=now - ABANDONED_DRAFT_INACTIVITY, now=now, batch_size=size
            ),
            receipts_cleared=self._repository.delete_receipt_data(
                finished_cutoff=now - RECEIPT_DATA_RETENTION, now=now, batch_size=size
            ),
        )
