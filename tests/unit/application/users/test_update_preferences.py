from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from mintflow.application.users import PreferencesUserNotFound, UpdatePreferences
from mintflow.domain.capture import CurrencyCode
from mintflow.domain.user import Locale, Timezone, User

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user
        self.saved: list[User] = []

    def get(self, user_id: UUID) -> User | None:
        return self.user if self.user is not None and self.user.id == user_id else None

    def update_preferences(self, user: User) -> None:
        self.saved.append(user)


def _user() -> User:
    return User.create(now=NOW, timezone=Timezone("UTC"), default_currency=CurrencyCode("EUR"))


def test_all_three_preferences_change_together() -> None:
    user = _user()
    users = FakeUsers(user)

    updated = UpdatePreferences(repository=users).execute(
        user_id=user.id, timezone="Asia/Tokyo", default_currency="PLN", locale="de-DE"
    )

    assert (updated.timezone, updated.default_currency, updated.locale) == (
        Timezone("Asia/Tokyo"),
        CurrencyCode("PLN"),
        Locale("de-DE"),
    )
    assert users.saved == [updated]


def test_currency_and_locale_can_be_cleared() -> None:
    user = _user()

    updated = UpdatePreferences(repository=FakeUsers(user)).execute(
        user_id=user.id, timezone="UTC", default_currency=None, locale=None
    )

    assert (updated.default_currency, updated.locale) == (None, None)


@pytest.mark.parametrize(
    "values",
    [
        {"timezone": "Mars/Base", "default_currency": "PLN", "locale": "de-DE"},
        {"timezone": "Asia/Tokyo", "default_currency": "XYZ", "locale": "de-DE"},
        {"timezone": "Asia/Tokyo", "default_currency": "PLN", "locale": "not a tag"},
    ],
)
def test_one_invalid_value_changes_nothing(values: dict[str, str]) -> None:
    user = _user()
    users = FakeUsers(user)

    with pytest.raises(ValueError):
        UpdatePreferences(repository=users).execute(user_id=user.id, **values)

    assert users.saved == []


def test_unchanged_preferences_write_nothing() -> None:
    user = _user()
    users = FakeUsers(user)

    UpdatePreferences(repository=users).execute(
        user_id=user.id, timezone="UTC", default_currency="EUR", locale=None
    )

    assert users.saved == []


def test_a_missing_user_is_reported() -> None:
    with pytest.raises(PreferencesUserNotFound):
        UpdatePreferences(repository=FakeUsers(None)).execute(
            user_id=uuid4(), timezone="UTC", default_currency=None, locale=None
        )
