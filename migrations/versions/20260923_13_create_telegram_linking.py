"""Create Telegram link challenges and connections.

Revision ID: 20260923_13
Revises: 20260923_12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260923_13"
down_revision: str | None = "20260923_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_EVENT_TYPES = (
    "event_type IN ('login_challenge_requested', 'login_succeeded', 'login_failed', "
    "'current_session_revoked', 'all_sessions_revoked')"
)
_NEW_EVENT_TYPES = (
    "event_type IN ('login_challenge_requested', 'login_succeeded', 'login_failed', "
    "'current_session_revoked', 'all_sessions_revoked', 'telegram_link_claimed', "
    "'telegram_linked', 'telegram_unlinked')"
)


def upgrade() -> None:
    op.create_unique_constraint("uq_web_sessions_id_user_id", "web_sessions", ["id", "user_id"])

    op.drop_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", type_="check"
    )
    op.create_check_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", _NEW_EVENT_TYPES
    )

    op.create_table(
        "telegram_link_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("initiating_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiating_web_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("claimed_telegram_display_name", sa.String(length=128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name="ck_telegram_link_challenges_token_hash_length"
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_telegram_link_challenges_expiry"),
        sa.CheckConstraint(
            "expires_at <= issued_at + INTERVAL '5 minutes'",
            name="ck_telegram_link_challenges_max_lifetime",
        ),
        sa.CheckConstraint(
            "(claimed_at IS NULL) = (claimed_telegram_user_id IS NULL)",
            name="ck_telegram_link_challenges_complete_claim",
        ),
        sa.CheckConstraint(
            "claimed_at IS NULL OR claimed_at >= issued_at",
            name="ck_telegram_link_challenges_claimed_after_issue",
        ),
        sa.CheckConstraint(
            "claimed_telegram_display_name IS NULL OR claimed_at IS NOT NULL",
            name="ck_telegram_link_challenges_display_name_needs_claim",
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR (claimed_at IS NOT NULL AND confirmed_at >= claimed_at)",
            name="ck_telegram_link_challenges_confirmation_needs_claim",
        ),
        sa.ForeignKeyConstraint(
            ["initiating_user_id"],
            ["users.id"],
            name="fk_telegram_link_challenges_initiating_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiating_web_session_id", "initiating_user_id"],
            ["web_sessions.id", "web_sessions.user_id"],
            name="fk_telegram_link_challenges_session_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_link_challenges"),
        sa.UniqueConstraint("token_hash", name="uq_telegram_link_challenges_token_hash"),
    )
    op.create_index(
        "ix_telegram_link_challenges_expires_at", "telegram_link_challenges", ["expires_at"]
    )
    op.create_index(
        "ix_telegram_link_challenges_initiating_user_id",
        "telegram_link_challenges",
        ["initiating_user_id"],
    )

    op.create_table(
        "telegram_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_display_name", sa.String(length=128), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "unlinked_at IS NULL OR unlinked_at >= linked_at",
            name="ck_telegram_connections_unlinked_after_linked",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_telegram_connections_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_connections"),
    )
    op.create_index(
        "uq_telegram_connections_active_user_id",
        "telegram_connections",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("unlinked_at IS NULL"),
    )
    op.create_index(
        "uq_telegram_connections_active_telegram_user_id",
        "telegram_connections",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("unlinked_at IS NULL"),
    )
    op.create_index("ix_telegram_connections_unlinked_at", "telegram_connections", ["unlinked_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_connections_unlinked_at", table_name="telegram_connections")
    op.drop_index(
        "uq_telegram_connections_active_telegram_user_id", table_name="telegram_connections"
    )
    op.drop_index("uq_telegram_connections_active_user_id", table_name="telegram_connections")
    op.drop_table("telegram_connections")
    op.drop_index(
        "ix_telegram_link_challenges_initiating_user_id", table_name="telegram_link_challenges"
    )
    op.drop_index("ix_telegram_link_challenges_expires_at", table_name="telegram_link_challenges")
    op.drop_table("telegram_link_challenges")
    # Existing Telegram audit rows would violate the old constraint. Keep them rather than
    # delete audit evidence: restore the constraint NOT VALID, so it binds new rows only.
    op.drop_constraint(
        "ck_auth_audit_records_event_type", "authentication_audit_records", type_="check"
    )
    op.execute(
        "ALTER TABLE authentication_audit_records ADD CONSTRAINT "
        f"ck_auth_audit_records_event_type CHECK ({_OLD_EVENT_TYPES}) NOT VALID"
    )
    op.drop_constraint("uq_web_sessions_id_user_id", "web_sessions", type_="unique")
