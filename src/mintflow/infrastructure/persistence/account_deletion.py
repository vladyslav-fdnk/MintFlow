"""One-transaction account deletion in dependency order (docs/account_deletion_design.md, A1).

Foreign keys stay RESTRICT, so every row goes by an explicit statement here and nothing is ever
removed by an accidental cascade. The user row is locked first: a concurrent deletion waits and
then finds nothing to delete, and a concurrent write for the user (a receipt, an edit) either
commits before the lock or fails its own foreign-key check afterwards.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from mintflow.application.users.deletion import AccountDeletion

# Each statement takes :user_id. Drafts and expenses reference each other, so one statement
# deletes both; PostgreSQL checks those references at its end.
_DELETE_OWNED_ROWS = (
    "DELETE FROM telegram_conversations WHERE user_id = :user_id",
    """
    DELETE FROM expense_change_records
    WHERE actor_user_id = :user_id
       OR expense_id IN (SELECT id FROM expenses WHERE owner_id = :user_id)
    """,
    """
    WITH expenses_gone AS (DELETE FROM expenses WHERE owner_id = :user_id RETURNING id)
    DELETE FROM capture_drafts WHERE owner_id = :user_id
    """,
    "DELETE FROM recognition_results WHERE owner_id = :user_id",
    # Receipt images go with their receipts (ON DELETE CASCADE, receipt design R3).
    "DELETE FROM receipts WHERE owner_id = :user_id",
    "DELETE FROM telegram_link_challenges WHERE initiating_user_id = :user_id",
    "DELETE FROM telegram_connections WHERE user_id = :user_id",
    "DELETE FROM web_sessions WHERE user_id = :user_id",
    """
    DELETE FROM login_challenges
    WHERE canonical_email IN (
        SELECT canonical_email FROM email_identities WHERE user_id = :user_id
    )
    """,
    "DELETE FROM email_identities WHERE user_id = :user_id",
    "UPDATE authentication_audit_records SET user_id = NULL WHERE user_id = :user_id",
    "DELETE FROM users WHERE id = :user_id",
)


class PostgreSQLAccountDeletionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_account(self, deletion: AccountDeletion) -> bool:
        """Commit everything or nothing. Joins a transaction the session already has open (a
        request that has read the session and user), so the lock and the deletes are atomic."""
        try:
            deleted = self._delete(deletion)
        except BaseException:
            self._session.rollback()
            raise
        self._session.commit()
        return deleted

    def _delete(self, deletion: AccountDeletion) -> bool:
        parameters = {"user_id": deletion.user_id}
        locked = self._session.execute(
            text("SELECT id FROM users WHERE id = :user_id FOR UPDATE"), parameters
        ).first()
        if locked is None:
            return False
        for statement in _DELETE_OWNED_ROWS:
            self._session.execute(text(statement), parameters)
        self._session.execute(
            text(
                """
                INSERT INTO deleted_accounts (user_id, deleted_at)
                VALUES (:user_id, :deleted_at)
                ON CONFLICT (user_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at
                """
            ),
            {**parameters, "deleted_at": deletion.deleted_at},
        )
        audit = deletion.audit_record
        self._session.execute(
            text(
                """
                INSERT INTO authentication_audit_records
                    (id, occurred_at, event_type, outcome, user_id, subject_record_id)
                VALUES (:id, :occurred_at, :event_type, :outcome, NULL, NULL)
                """
            ),
            {
                "id": audit.id,
                "occurred_at": audit.occurred_at,
                "event_type": audit.event_type.value,
                "outcome": audit.outcome.value,
            },
        )
        return True
