import pytest
from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from mintflow.domain.capture import UNCATEGORIZED_KEY
from mintflow.infrastructure.persistence import SqlAlchemyCategoryRepository
from mintflow.infrastructure.persistence.models import CategoryRecord

pytestmark = pytest.mark.integration

_EXPECTED_KEYS = {
    "groceries",
    "food_and_dining",
    "transport",
    "shopping",
    "housing",
    "utilities",
    "health",
    "entertainment",
    "travel",
    "education",
    "gifts",
    "other",
    "uncategorized",
}


def test_seed_produces_exactly_the_expected_system_categories(db_session: Session) -> None:
    keys = set(db_session.scalars(select(CategoryRecord.key)))

    assert keys == _EXPECTED_KEYS


def test_uncategorized_is_present_and_active_after_seeding(db_session: Session) -> None:
    record = db_session.scalar(
        select(CategoryRecord).where(CategoryRecord.key == UNCATEGORIZED_KEY)
    )

    assert record is not None
    assert record.is_active is True


def test_reseeding_is_idempotent(db_session: Session) -> None:
    existing = list(
        db_session.execute(select(CategoryRecord.id, CategoryRecord.key, CategoryRecord.name)).all()
    )
    seed_statement = postgresql.insert(CategoryRecord).on_conflict_do_nothing(
        index_elements=["key"]
    )
    db_session.execute(
        seed_statement.values(
            [
                {"id": row.id, "key": row.key, "name": row.name, "is_active": True}
                for row in existing
            ]
        )
    )
    db_session.flush()

    assert db_session.scalar(select(func.count()).select_from(CategoryRecord)) == len(
        _EXPECTED_KEYS
    )


def test_get_by_key_returns_existing_and_none_for_unknown(db_session: Session) -> None:
    repository = SqlAlchemyCategoryRepository(db_session)

    groceries = repository.get_by_key("groceries")
    assert groceries is not None
    assert groceries.key == "groceries"
    assert groceries.name == "Groceries"

    assert repository.get_by_key("not-a-real-key") is None


def test_list_active_excludes_a_deactivated_category(db_session: Session) -> None:
    repository = SqlAlchemyCategoryRepository(db_session)
    db_session.execute(
        update(CategoryRecord).where(CategoryRecord.key == "gifts").values(is_active=False)
    )
    db_session.flush()

    active_keys = {category.key for category in repository.list_active()}

    assert "gifts" not in active_keys
    assert active_keys == _EXPECTED_KEYS - {"gifts"}
