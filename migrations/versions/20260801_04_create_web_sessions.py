"""Create Web sessions.

Revision ID: 20260801_04
Revises: 20260801_03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_04"
down_revision: str | None = "20260801_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32", name="ck_web_sessions_secret_hash_length"
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_web_sessions_expiry"),
        sa.CheckConstraint(
            "expires_at <= issued_at + INTERVAL '30 days'",
            name="ck_web_sessions_max_lifetime",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_web_sessions_revoked_at_not_before_issued_at",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_web_sessions_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_web_sessions"),
        sa.UniqueConstraint("secret_hash", name="uq_web_sessions_secret_hash"),
    )
    op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"])
    op.create_index("ix_web_sessions_revoked_at", "web_sessions", ["revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_web_sessions_revoked_at", table_name="web_sessions")
    op.drop_index("ix_web_sessions_expires_at", table_name="web_sessions")
    op.drop_table("web_sessions")
