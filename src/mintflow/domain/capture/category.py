from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

# The system Category that Expense confirmation falls back to when no
# explicit selection was made (decision 13). Nothing in this domain may
# deactivate or remove the Category with this key.
UNCATEGORIZED_KEY: Final[str] = "uncategorized"


@dataclass(frozen=True, slots=True)
class Category:
    """A stable classification identity for Expenses.

    System categories only in the MVP (decision 12): not owned by
    individual Users, and referentially stable for historical Expenses
    even after deactivation.
    """

    id: UUID
    key: str
    name: str
    is_active: bool

    def __post_init__(self) -> None:
        if self.key != self.key.strip().lower() or not self.key:
            raise ValueError("category key must be a non-empty, stable lowercase identifier")
        if not self.name.strip():
            raise ValueError("category name must not be empty")
        if self.key == UNCATEGORIZED_KEY and not self.is_active:
            raise ValueError("the uncategorized category must always be active")

    def deactivate(self) -> "Category":
        if self.key == UNCATEGORIZED_KEY:
            raise ValueError("the uncategorized category cannot be deactivated")
        if not self.is_active:
            return self
        return replace(self, is_active=False)

    def activate(self) -> "Category":
        if self.is_active:
            return self
        return replace(self, is_active=True)
