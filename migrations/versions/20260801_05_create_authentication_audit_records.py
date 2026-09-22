"""create authentication audit records

Revision ID: 20260801_05
Revises: 20260801_04
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_05"
down_revision: str | None = "20260801_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authentication_audit_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('login_challenge_requested', 'login_succeeded', 'login_failed', "
            "'current_session_revoked', 'all_sessions_revoked')",
            name="ck_auth_audit_records_event_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed')", name="ck_auth_audit_records_outcome"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_audit_records_occurred_at",
        "authentication_audit_records",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_audit_records_occurred_at", table_name="authentication_audit_records")
    op.drop_table("authentication_audit_records")
