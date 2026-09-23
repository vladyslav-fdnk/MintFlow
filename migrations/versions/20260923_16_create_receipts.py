"""Create receipts, receipt images, and recognition results; link drafts and expenses.

Revision ID: 20260923_16
Revises: 20260923_15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260923_16"
down_revision: str | None = "20260923_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_RECEIPT_IMAGE_BYTES = 10 * 1024 * 1024


def upgrade() -> None:
    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=256), nullable=True),
        sa.Column("image_removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued', 'processing', 'recognized', 'recognition_failed')",
            name="ck_receipts_state",
        ),
        sa.CheckConstraint(
            "(state = 'queued') = (attempt_id IS NULL)", name="ck_receipts_attempt_after_queue"
        ),
        sa.CheckConstraint(
            "(state = 'processing') = (lease_expires_at IS NOT NULL)",
            name="ck_receipts_lease_while_processing",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_receipts_owner_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_receipts"),
    )
    op.create_index(
        "ix_receipts_claimable",
        "receipts",
        ["created_at"],
        postgresql_where=sa.text("state IN ('queued', 'processing') AND image_removed_at IS NULL"),
    )
    op.create_index("ix_receipts_owner_id", "receipts", ["owner_id"])

    op.create_table(
        "receipt_images",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "media_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_receipt_images_media_type",
        ),
        sa.CheckConstraint(
            f"octet_length(content) BETWEEN 1 AND {MAX_RECEIPT_IMAGE_BYTES}",
            name="ck_receipt_images_size",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["receipts.id"],
            name="fk_receipt_images_receipt_id_receipts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_receipt_images"),
    )

    op.create_table(
        "recognition_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_name", sa.String(length=140), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("total_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("total_currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(total_minor_units IS NULL) = (total_currency IS NULL)",
            name="ck_recognition_results_total_fields_together",
        ),
        sa.CheckConstraint(
            "total_minor_units IS NULL OR total_minor_units > 0",
            name="ck_recognition_results_total_positive",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["receipts.id"],
            name="fk_recognition_results_receipt_id_receipts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_recognition_results_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recognition_results"),
        sa.UniqueConstraint("receipt_id", "attempt_id", name="uq_recognition_results_attempt"),
    )

    op.add_column(
        "capture_drafts", sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "capture_drafts",
        sa.Column("recognition_result_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_capture_drafts_receipt_id_receipts",
        "capture_drafts",
        "receipts",
        ["receipt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_capture_drafts_recognition_result_id_recognition_results",
        "capture_drafts",
        "recognition_results",
        ["recognition_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_capture_drafts_receipt_id", "capture_drafts", ["receipt_id"])
    op.create_check_constraint(
        "ck_capture_drafts_receipt_matches_source",
        "capture_drafts",
        "(source = 'telegram_receipt') = (receipt_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_expenses_receipt_id_receipts",
        "expenses",
        "receipts",
        ["receipt_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_expenses_receipt_id_receipts", "expenses", type_="foreignkey")
    op.drop_constraint("ck_capture_drafts_receipt_matches_source", "capture_drafts", type_="check")
    op.drop_constraint("uq_capture_drafts_receipt_id", "capture_drafts", type_="unique")
    op.drop_constraint(
        "fk_capture_drafts_recognition_result_id_recognition_results",
        "capture_drafts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_capture_drafts_receipt_id_receipts", "capture_drafts", type_="foreignkey"
    )
    op.drop_column("capture_drafts", "recognition_result_id")
    op.drop_column("capture_drafts", "receipt_id")
    op.drop_table("recognition_results")
    op.drop_table("receipt_images")
    op.drop_index("ix_receipts_owner_id", table_name="receipts")
    op.drop_index("ix_receipts_claimable", table_name="receipts")
    op.drop_table("receipts")
