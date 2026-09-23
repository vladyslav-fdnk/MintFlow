"""Create the append-only expense_change_records table.

Revision ID: 20260923_12
Revises: 20260923_11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260923_12"
down_revision: str | None = "20260923_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expense_change_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_type", sa.String(length=16), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "change_type IN ('edited', 'deleted', 'restored')",
            name="ck_expense_change_records_change_type",
        ),
        sa.CheckConstraint(
            "(change_type = 'edited' AND changes IS NOT NULL "
            "AND jsonb_typeof(changes) = 'object' AND changes <> '{}'::jsonb) "
            "OR (change_type <> 'edited' AND changes IS NULL)",
            name="ck_expense_change_records_changes_match_type",
        ),
        sa.ForeignKeyConstraint(
            ["expense_id"],
            ["expenses.id"],
            name="fk_expense_change_records_expense_id_expenses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_expense_change_records_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_expense_change_records"),
    )
    op.create_index(
        "ix_expense_change_records_expense_id_occurred_at",
        "expense_change_records",
        ["expense_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_expense_change_records_expense_id_occurred_at",
        table_name="expense_change_records",
    )
    op.drop_table("expense_change_records")
