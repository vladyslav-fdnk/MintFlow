"""Create login challenges.

Revision ID: 20260801_02
Revises: 20260801_01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_02"
down_revision: str | None = "20260801_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_target", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="ck_login_challenges_expiry",
        ),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name="ck_login_challenges_token_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_login_challenges"),
        sa.UniqueConstraint("token_hash", name="uq_login_challenges_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("login_challenges")
