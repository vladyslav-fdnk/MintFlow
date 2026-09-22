"""Create authentication rate-limit buckets.

Revision ID: 20260801_03
Revises: 20260801_02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_03"
down_revision: str | None = "20260801_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authentication_rate_limit_buckets",
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("key_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "dimension IN ('email_delivery', 'network_request')",
            name="ck_auth_rate_limit_buckets_dimension",
        ),
        sa.CheckConstraint(
            "octet_length(key_digest) = 32", name="ck_auth_rate_limit_key_digest_length"
        ),
        sa.CheckConstraint("count > 0", name="ck_auth_rate_limit_buckets_count_positive"),
        sa.CheckConstraint(
            "expires_at > window_started_at AND "
            "expires_at <= window_started_at + INTERVAL '24 hours'",
            name="ck_auth_rate_limit_buckets_expiry",
        ),
        sa.PrimaryKeyConstraint(
            "dimension",
            "key_digest",
            "window_started_at",
            name="pk_authentication_rate_limit_buckets",
        ),
    )
    op.create_index(
        "ix_auth_rate_limit_buckets_expires_at",
        "authentication_rate_limit_buckets",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_rate_limit_buckets_expires_at",
        table_name="authentication_rate_limit_buckets",
    )
    op.drop_table("authentication_rate_limit_buckets")
