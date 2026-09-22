"""Add LoginChallenge retention indexes.

Revision ID: 20260802_06
Revises: 20260801_05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_06"
down_revision: str | None = "20260801_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_login_challenges_consumed_at",
        "login_challenges",
        ["consumed_at"],
    )
    op.create_index(
        "ix_login_challenges_expires_at",
        "login_challenges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_login_challenges_expires_at", table_name="login_challenges")
    op.drop_index("ix_login_challenges_consumed_at", table_name="login_challenges")
