"""Create the Telegram processed-update ledger used for deduplication.

Revision ID: 20260923_14
Revises: 20260923_13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_14"
down_revision: str | None = "20260923_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_processed_updates",
        sa.Column("update_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("update_id", name="pk_telegram_processed_updates"),
    )
    op.create_index(
        "ix_telegram_processed_updates_processed_at",
        "telegram_processed_updates",
        ["processed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_processed_updates_processed_at", table_name="telegram_processed_updates"
    )
    op.drop_table("telegram_processed_updates")
