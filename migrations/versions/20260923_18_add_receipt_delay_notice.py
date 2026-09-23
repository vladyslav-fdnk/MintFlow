"""Remember that a receipt's "taking longer" message was sent, so it is sent at most once.

Revision ID: 20260923_18
Revises: 20260923_17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_18"
down_revision: str | None = "20260923_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column("delay_notice_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("receipts", "delay_notice_sent_at")
