from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class PostgreSQLTelegramRetentionRepository:
    """Batched, skip-locked deletes and updates; each call is its own transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_processed_updates(self, *, processed_cutoff: datetime, batch_size: int) -> int:
        return self._run(
            """
            WITH candidates AS (
                SELECT update_id
                FROM telegram_processed_updates
                WHERE processed_at <= :cutoff
                ORDER BY processed_at, update_id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM telegram_processed_updates AS target
            USING candidates
            WHERE target.update_id = candidates.update_id
            RETURNING target.update_id
            """,
            {"cutoff": processed_cutoff, "batch_size": batch_size},
        )

    def delete_link_challenges(self, *, finished_cutoff: datetime, batch_size: int) -> int:
        """Confirmed challenges 30 days after confirmation, others 30 days after expiry."""
        return self._run(
            """
            WITH candidates AS (
                SELECT id
                FROM telegram_link_challenges
                WHERE (confirmed_at IS NOT NULL AND confirmed_at <= :cutoff)
                   OR (confirmed_at IS NULL AND expires_at <= :cutoff)
                ORDER BY COALESCE(confirmed_at, expires_at), id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM telegram_link_challenges AS target
            USING candidates
            WHERE target.id = candidates.id
            RETURNING target.id
            """,
            {"cutoff": finished_cutoff, "batch_size": batch_size},
        )

    def delete_unlinked_connections(self, *, unlinked_cutoff: datetime, batch_size: int) -> int:
        """Only unlinked rows; an active connection (unlinked_at IS NULL) never matches."""
        return self._run(
            """
            WITH candidates AS (
                SELECT id
                FROM telegram_connections
                WHERE unlinked_at IS NOT NULL AND unlinked_at <= :cutoff
                ORDER BY unlinked_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM telegram_connections AS target
            USING candidates
            WHERE target.id = candidates.id
            RETURNING target.id
            """,
            {"cutoff": unlinked_cutoff, "batch_size": batch_size},
        )

    def expire_abandoned_drafts(
        self, *, inactive_cutoff: datetime, now: datetime, batch_size: int
    ) -> int:
        """Expire open drafts idle since the cutoff and release conversations pointing at them.

        The UPDATE re-checks state and inactivity, so a draft confirmed, cancelled, or
        edited while this statement waited for its row is left alone.
        """
        return self._run(
            """
            WITH candidates AS (
                SELECT id
                FROM capture_drafts
                WHERE state IN ('collecting', 'ready_for_review')
                  AND modified_at <= :cutoff
                ORDER BY modified_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            ),
            expired AS (
                UPDATE capture_drafts AS target
                SET state = 'expired', modified_at = :now
                FROM candidates
                WHERE target.id = candidates.id
                  AND target.state IN ('collecting', 'ready_for_review')
                  AND target.modified_at <= :cutoff
                RETURNING target.id
            ),
            released AS (
                UPDATE telegram_conversations AS conversation
                SET active_draft_id = NULL, awaiting = 'nothing', updated_at = :now
                FROM expired
                WHERE conversation.active_draft_id = expired.id
                RETURNING conversation.user_id
            )
            SELECT id FROM expired
            """,
            {"cutoff": inactive_cutoff, "now": now, "batch_size": batch_size},
        )

    def _run(self, statement: str, parameters: dict[str, Any]) -> int:
        with self._session.begin():
            return len(self._session.execute(text(statement), parameters).all())
