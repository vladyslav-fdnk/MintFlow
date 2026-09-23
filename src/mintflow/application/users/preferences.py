"""Changing a User's conventions (web design W8; MVP section 6, settings).

Locale, timezone, and default currency change together or not at all: ``User.update_preferences``
validates every value before applying any. The UI language stays English until translations
exist, so it is not offered here.
"""

from typing import Protocol
from uuid import UUID

from mintflow.domain.user import User


class PreferencesUserNotFound(Exception):
    """The caller has no User record."""


class PreferencesRepository(Protocol):
    def get(self, user_id: UUID) -> User | None: ...

    def update_preferences(self, user: User) -> None: ...


class UpdatePreferences:
    def __init__(self, *, repository: PreferencesRepository) -> None:
        self._repository = repository

    def execute(
        self,
        *,
        user_id: UUID,
        timezone: str,
        default_currency: str | None,
        locale: str | None,
    ) -> User:
        """Raises ValueError, changing nothing, when any value is invalid."""
        user = self._repository.get(user_id)
        if user is None:
            raise PreferencesUserNotFound("user not found")
        updated = user.update_preferences(
            timezone=timezone, default_currency=default_currency, locale=locale
        )
        if updated != user:
            self._repository.update_preferences(updated)
        return updated
