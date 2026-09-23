"""Add the partial index serving the keyset Expense history listing.

Revision ID: 20260923_11
Revises: 20260806_10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_11"
down_revision: str | None = "20260806_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_expenses_active_history",
        "expenses",
        [
            "owner_id",
            sa.text("transaction_date DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_expenses_active_history", table_name="expenses")
