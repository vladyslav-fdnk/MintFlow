"""Keep the latest exchange rate per currency, in units per euro.

Revision ID: 20260924_19
Revises: 20260923_18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_19"
down_revision: str | None = "20260923_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_rates",
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("units_per_eur", sa.Numeric(24, 10), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("units_per_eur > 0", name="ck_exchange_rates_positive"),
        sa.CheckConstraint("source IN ('ECB', 'NBU')", name="ck_exchange_rates_source"),
        sa.PrimaryKeyConstraint("currency", name="pk_exchange_rates"),
    )


def downgrade() -> None:
    op.drop_table("exchange_rates")
