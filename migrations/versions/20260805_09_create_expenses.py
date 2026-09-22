"""Create expenses and add the deferred CaptureDraft -> Expense foreign key.

Revision ID: 20260805_09
Revises: 20260804_08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_09"
down_revision: str | None = "20260804_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("amount_currency", sa.String(length=3), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("merchant_name", sa.String(length=140), nullable=True),
        sa.Column("category_key", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("capture_draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "amount_minor_units > 0",
            name="ck_expenses_amount_positive",
        ),
        sa.CheckConstraint(
            "source IN ('telegram_manual', 'telegram_receipt', 'web_manual')",
            name="ck_expenses_source",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_expenses_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_key"],
            ["categories.key"],
            name="fk_expenses_category_key_categories",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["capture_draft_id"],
            ["capture_drafts.id"],
            name="fk_expenses_capture_draft_id_capture_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_expenses"),
        sa.UniqueConstraint("capture_draft_id", name="uq_expenses_capture_draft_id"),
    )
    op.create_index("ix_expenses_owner_id", "expenses", ["owner_id"], unique=False)

    # Deferred from CAPTURE-04: capture_drafts.expense_id could not reference
    # a table that did not exist yet.
    op.create_foreign_key(
        "fk_capture_drafts_expense_id_expenses",
        "capture_drafts",
        "expenses",
        ["expense_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_capture_drafts_expense_id_expenses", "capture_drafts", type_="foreignkey"
    )
    op.drop_index("ix_expenses_owner_id", table_name="expenses")
    op.drop_table("expenses")
