"""Create capture_drafts.

Revision ID: 20260804_08
Revises: 20260803_07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_08"
down_revision: str | None = "20260803_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capture_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("amount_currency", sa.String(length=3), nullable=True),
        sa.Column("amount_source", sa.String(length=16), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("transaction_date_source", sa.String(length=16), nullable=True),
        sa.Column("merchant_name", sa.String(length=140), nullable=True),
        sa.Column("merchant_source", sa.String(length=16), nullable=True),
        sa.Column("category_key", sa.String(length=32), nullable=True),
        sa.Column("category_key_source", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_capture_drafts_revision_non_negative",
        ),
        sa.CheckConstraint(
            "source IN ('telegram_manual', 'telegram_receipt', 'web_manual')",
            name="ck_capture_drafts_source",
        ),
        sa.CheckConstraint(
            "state IN ('collecting', 'awaiting_recognition', 'ready_for_review', "
            "'confirmed', 'cancelled', 'expired')",
            name="ck_capture_drafts_state",
        ),
        sa.CheckConstraint(
            "(state = 'confirmed') = (expense_id IS NOT NULL)",
            name="ck_capture_drafts_confirmed_requires_expense_id",
        ),
        sa.CheckConstraint(
            "(state = 'confirmed') = (confirmed_at IS NOT NULL)",
            name="ck_capture_drafts_confirmed_requires_confirmed_at",
        ),
        sa.CheckConstraint(
            "(amount_minor_units IS NULL) = (amount_currency IS NULL) AND "
            "(amount_minor_units IS NULL) = (amount_source IS NULL)",
            name="ck_capture_drafts_amount_fields_together",
        ),
        sa.CheckConstraint(
            "(transaction_date IS NULL) = (transaction_date_source IS NULL)",
            name="ck_capture_drafts_transaction_date_fields_together",
        ),
        sa.CheckConstraint(
            "(merchant_name IS NULL) = (merchant_source IS NULL)",
            name="ck_capture_drafts_merchant_fields_together",
        ),
        sa.CheckConstraint(
            "(category_key IS NULL) = (category_key_source IS NULL)",
            name="ck_capture_drafts_category_key_fields_together",
        ),
        sa.CheckConstraint(
            "amount_source IS NULL OR amount_source IN ('recognition', 'user', 'default')",
            name="ck_capture_drafts_amount_source",
        ),
        sa.CheckConstraint(
            "transaction_date_source IS NULL OR "
            "transaction_date_source IN ('recognition', 'user', 'default')",
            name="ck_capture_drafts_transaction_date_source",
        ),
        sa.CheckConstraint(
            "merchant_source IS NULL OR merchant_source IN ('recognition', 'user', 'default')",
            name="ck_capture_drafts_merchant_source",
        ),
        sa.CheckConstraint(
            "category_key_source IS NULL OR "
            "category_key_source IN ('recognition', 'user', 'default')",
            name="ck_capture_drafts_category_key_source",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_capture_drafts_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_capture_drafts"),
    )
    op.create_index("ix_capture_drafts_owner_id", "capture_drafts", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_capture_drafts_owner_id", table_name="capture_drafts")
    op.drop_table("capture_drafts")
