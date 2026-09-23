"""Remember a receipt photo sent while another draft is active.

Revision ID: 20260923_17
Revises: 20260923_16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_17"
down_revision: str | None = "20260923_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_conversations",
        sa.Column("pending_receipt_file_id", sa.String(length=256), nullable=True),
    )
    op.create_check_constraint(
        "ck_telegram_conversations_pending_receipt_needs_draft",
        "telegram_conversations",
        "pending_receipt_file_id IS NULL OR active_draft_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_telegram_conversations_pending_receipt_needs_draft",
        "telegram_conversations",
        type_="check",
    )
    op.drop_column("telegram_conversations", "pending_receipt_file_id")
