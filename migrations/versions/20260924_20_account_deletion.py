"""Support account deletion: an anonymous audit event, SET NULL audit users, tombstones.

Additive and compatible with the previous release (AGENTS.md): the previous code never writes
account_deleted, never deletes users, and never reads deleted_accounts.

Revision ID: 20260924_20
Revises: 20260924_19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_20"
down_revision: str | None = "20260924_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES_BEFORE = (
    "event_type IN ('login_challenge_requested', 'login_succeeded', 'login_failed', "
    "'current_session_revoked', 'all_sessions_revoked', 'telegram_link_claimed', "
    "'telegram_linked', 'telegram_unlinked')"
)
_EVENT_TYPES_AFTER = (
    "event_type IN ('login_challenge_requested', 'login_succeeded', 'login_failed', "
    "'current_session_revoked', 'all_sessions_revoked', 'telegram_link_claimed', "
    "'telegram_linked', 'telegram_unlinked', 'account_deleted')"
)
_AUDIT_USER_FK = "authentication_audit_records_user_id_fkey"


def _audit_user_foreign_key(ondelete: str) -> None:
    op.drop_constraint(_AUDIT_USER_FK, "authentication_audit_records", type_="foreignkey")
    op.create_foreign_key(
        _AUDIT_USER_FK,
        "authentication_audit_records",
        "users",
        ["user_id"],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", type_="check"
    )
    op.create_check_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", _EVENT_TYPES_AFTER
    )
    _audit_user_foreign_key("SET NULL")
    op.create_table(
        "deleted_accounts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_deleted_accounts_deleted_at", "deleted_accounts", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_deleted_accounts_deleted_at", table_name="deleted_accounts")
    op.drop_table("deleted_accounts")
    _audit_user_foreign_key("RESTRICT")
    op.execute("DELETE FROM authentication_audit_records WHERE event_type = 'account_deleted'")
    op.drop_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", type_="check"
    )
    op.create_check_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", _EVENT_TYPES_BEFORE
    )
