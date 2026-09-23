"""Create per-user Telegram conversation state.

Revision ID: 20260923_15
Revises: 20260923_14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260923_15"
down_revision: str | None = "20260923_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_conversations",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("awaiting", sa.String(length=16), nullable=False),
        sa.Column("currency_is_default", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "awaiting IN ('nothing', 'amount', 'currency', 'merchant', 'category', 'date')",
            name="ck_telegram_conversations_awaiting",
        ),
        sa.CheckConstraint(
            "active_draft_id IS NOT NULL OR awaiting = 'nothing'",
            name="ck_telegram_conversations_waiting_needs_draft",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_telegram_conversations_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["active_draft_id"],
            ["capture_drafts.id"],
            name="fk_telegram_conversations_active_draft_id_capture_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_telegram_conversations"),
    )


def downgrade() -> None:
    op.drop_table("telegram_conversations")
