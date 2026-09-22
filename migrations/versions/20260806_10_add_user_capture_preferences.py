"""Add User capture preferences: timezone, default currency, UI language, locale.

Revision ID: 20260806_10
Revises: 20260805_09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_10"
down_revision: str | None = "20260805_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
    )
    op.add_column(
        "users",
        sa.Column("default_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("ui_language", sa.String(length=8), nullable=False, server_default="en"),
    )
    op.add_column(
        "users",
        sa.Column("locale", sa.String(length=35), nullable=True),
    )
    op.alter_column("users", "timezone", server_default=None)
    op.alter_column("users", "ui_language", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "locale")
    op.drop_column("users", "ui_language")
    op.drop_column("users", "default_currency")
    op.drop_column("users", "timezone")
