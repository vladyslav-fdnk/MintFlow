from uuid import uuid4

import pytest

from mintflow.domain.capture import UNCATEGORIZED_KEY, Category


def _category(key: str = "groceries", *, is_active: bool = True) -> Category:
    return Category(id=uuid4(), key=key, name=key.title(), is_active=is_active)


def test_constructs_an_active_category() -> None:
    category = _category()

    assert category.is_active is True


def test_rejects_non_lowercase_or_empty_key() -> None:
    with pytest.raises(ValueError, match="stable lowercase identifier"):
        Category(id=uuid4(), key="Groceries", name="Groceries", is_active=True)
    with pytest.raises(ValueError, match="stable lowercase identifier"):
        Category(id=uuid4(), key="", name="Groceries", is_active=True)


def test_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        Category(id=uuid4(), key="groceries", name="  ", is_active=True)


def test_rejects_an_inactive_uncategorized_category() -> None:
    with pytest.raises(ValueError, match="always be active"):
        Category(id=uuid4(), key=UNCATEGORIZED_KEY, name="Uncategorized", is_active=False)


def test_deactivate_then_activate_round_trips() -> None:
    category = _category()

    deactivated = category.deactivate()
    assert deactivated.is_active is False
    assert deactivated.key == category.key
    assert deactivated.id == category.id

    reactivated = deactivated.activate()
    assert reactivated.is_active is True


def test_deactivate_is_idempotent() -> None:
    category = _category(is_active=False)

    assert category.deactivate() is category


def test_activate_is_idempotent() -> None:
    category = _category(is_active=True)

    assert category.activate() is category


def test_uncategorized_cannot_be_deactivated() -> None:
    uncategorized = _category(key=UNCATEGORIZED_KEY)

    with pytest.raises(ValueError, match="cannot be deactivated"):
        uncategorized.deactivate()
