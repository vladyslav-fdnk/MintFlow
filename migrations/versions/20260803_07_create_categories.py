"""Create categories and seed the system category catalogue.

Revision ID: 20260803_07
Revises: 20260802_06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_07"
down_revision: str | None = "20260802_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed identities for the system category catalogue (decision 12), so the
# seed step is deterministic and reproducible across environments and
# across upgrade/downgrade/re-upgrade cycles. Keys are the stable semantic
# identity; names are only a display label.
_SYSTEM_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("e31f59a9-3244-429e-b0e4-2674528187d3", "groceries", "Groceries"),
    ("6df56bc4-b76c-4fad-bdaf-35d3ddb23d5e", "food_and_dining", "Food & Dining"),
    ("e881c8cf-5ee3-4d07-9ec7-fe6aff390495", "transport", "Transport"),
    ("05c0f48f-5264-479b-aaa0-c3077dab9aaa", "shopping", "Shopping"),
    ("80ea0bc8-e725-4fcb-9396-30730ad11d61", "housing", "Housing"),
    ("cf92fc61-e88e-4145-9f04-1b2535460e4a", "utilities", "Utilities"),
    ("9b16d4d9-a9b8-4721-ae3a-776b90f16d8d", "health", "Health"),
    ("a39276e3-6a93-4e40-936f-c466ecd38003", "entertainment", "Entertainment"),
    ("3945fcc4-79f0-4964-b6f6-0cb2618e20b4", "travel", "Travel"),
    ("18aaccb1-3885-4927-9a38-1907da9a5b65", "education", "Education"),
    ("0dc44c30-6d4c-4a8a-aba3-49ca6bb82da4", "gifts", "Gifts"),
    ("d3c3cbba-b92c-4512-bb08-e9d0fc7999e0", "other", "Other"),
    ("c8ec0852-6f86-4960-beb5-96ea88274047", "uncategorized", "Uncategorized"),
)


def upgrade() -> None:
    categories = op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("key", name="uq_categories_key"),
    )

    seed_statement = postgresql.insert(categories).on_conflict_do_nothing(index_elements=["key"])
    op.execute(
        seed_statement.values(
            [
                {"id": row_id, "key": key, "name": name, "is_active": True}
                for row_id, key, name in _SYSTEM_CATEGORIES
            ]
        )
    )


def downgrade() -> None:
    op.drop_table("categories")
