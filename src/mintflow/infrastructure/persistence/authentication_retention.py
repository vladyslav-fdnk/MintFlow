from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class PostgreSQLAuthenticationRetentionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_login_challenges(
        self, *, consumed_cutoff: datetime, expired_cutoff: datetime, batch_size: int
    ) -> int:
        return self._delete(
            """
            WITH candidates AS (
                SELECT id
                FROM login_challenges
                WHERE (consumed_at IS NOT NULL AND consumed_at <= :consumed_cutoff)
                   OR (consumed_at IS NULL AND expires_at <= :expired_cutoff)
                ORDER BY COALESCE(consumed_at, expires_at), id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM login_challenges AS target
            USING candidates
            WHERE target.id = candidates.id
            RETURNING target.id
            """,
            {
                "consumed_cutoff": consumed_cutoff,
                "expired_cutoff": expired_cutoff,
                "batch_size": batch_size,
            },
        )

    def delete_web_sessions(
        self, *, revoked_cutoff: datetime, expired_cutoff: datetime, batch_size: int
    ) -> int:
        return self._delete(
            """
            WITH candidates AS (
                SELECT id
                FROM web_sessions
                WHERE (revoked_at IS NOT NULL AND revoked_at <= :revoked_cutoff)
                   OR expires_at <= :expired_cutoff
                ORDER BY LEAST(COALESCE(revoked_at, expires_at), expires_at), id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM web_sessions AS target
            USING candidates
            WHERE target.id = candidates.id
            RETURNING target.id
            """,
            {
                "revoked_cutoff": revoked_cutoff,
                "expired_cutoff": expired_cutoff,
                "batch_size": batch_size,
            },
        )

    def delete_rate_limit_buckets(
        self, *, expired_at: datetime, maximum_age_at: datetime, batch_size: int
    ) -> int:
        return self._delete(
            """
            WITH candidates AS (
                SELECT dimension, key_digest, window_started_at
                FROM authentication_rate_limit_buckets
                WHERE expires_at <= :expired_at
                   OR window_started_at <= :maximum_age_at
                ORDER BY LEAST(expires_at, window_started_at + INTERVAL '24 hours'),
                         dimension, key_digest, window_started_at
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM authentication_rate_limit_buckets AS target
            USING candidates
            WHERE target.dimension = candidates.dimension
              AND target.key_digest = candidates.key_digest
              AND target.window_started_at = candidates.window_started_at
            RETURNING target.dimension
            """,
            {
                "expired_at": expired_at,
                "maximum_age_at": maximum_age_at,
                "batch_size": batch_size,
            },
        )

    def delete_authentication_audit_records(
        self, *, occurred_cutoff: datetime, batch_size: int
    ) -> int:
        return self._delete(
            """
            WITH candidates AS (
                SELECT id
                FROM authentication_audit_records
                WHERE occurred_at <= :occurred_cutoff
                ORDER BY occurred_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM authentication_audit_records AS target
            USING candidates
            WHERE target.id = candidates.id
            RETURNING target.id
            """,
            {"occurred_cutoff": occurred_cutoff, "batch_size": batch_size},
        )

    def restored_deleted_accounts(self, *, batch_size: int) -> list[UUID]:
        with self._session.begin():
            return list(
                self._session.scalars(
                    text(
                        """
                        SELECT users.id
                        FROM users JOIN deleted_accounts ON deleted_accounts.user_id = users.id
                        ORDER BY deleted_accounts.deleted_at, users.id
                        LIMIT :batch_size
                        """
                    ),
                    {"batch_size": batch_size},
                )
            )

    def delete_deleted_account_tombstones(
        self, *, deleted_cutoff: datetime, batch_size: int
    ) -> int:
        """Old tombstones only, and never one whose user exists again (it is deleted first)."""
        return self._delete(
            """
            WITH candidates AS (
                SELECT user_id
                FROM deleted_accounts
                WHERE deleted_at <= :deleted_cutoff
                  AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = deleted_accounts.user_id)
                ORDER BY deleted_at, user_id
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            DELETE FROM deleted_accounts AS target
            USING candidates
            WHERE target.user_id = candidates.user_id
            RETURNING target.user_id
            """,
            {"deleted_cutoff": deleted_cutoff, "batch_size": batch_size},
        )

    def _delete(self, statement: str, parameters: dict[str, Any]) -> int:
        with self._session.begin():
            result = self._session.execute(text(statement), parameters)
            deleted_count = len(result.all())
        return deleted_count
