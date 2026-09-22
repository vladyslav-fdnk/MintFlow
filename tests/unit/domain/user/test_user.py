from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mintflow.domain.capture import CurrencyCode
from mintflow.domain.user import Locale, Timezone, UILanguage, User, UserStatus

CREATED_AT = datetime(2026, 8, 1, 10, 30, tzinfo=UTC)


def test_creates_user_with_timezone_aware_datetime() -> None:
    user = User.create(now=CREATED_AT)

    assert user.status is UserStatus.ACTIVE
    assert user.created_at == CREATED_AT
    assert user.deactivated_at is None


def test_new_user_has_default_timezone_and_ui_language_and_no_other_preferences() -> None:
    user = User.create(now=CREATED_AT)

    assert user.timezone == Timezone("UTC")
    assert user.ui_language == UILanguage("en")
    assert user.default_currency is None
    assert user.locale is None


def test_rejects_naive_datetime_during_creation() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        User.create(now=datetime(2026, 8, 1, 10, 30))


def test_deactivates_user_at_timezone_aware_datetime() -> None:
    user = User.create(now=CREATED_AT)
    deactivated_at = CREATED_AT + timedelta(days=1)

    deactivated_user = user.deactivate(now=deactivated_at)

    assert deactivated_user.status is UserStatus.DEACTIVATED
    assert deactivated_user.deactivated_at == deactivated_at


def test_rejects_naive_datetime_during_deactivation() -> None:
    user = User.create(now=CREATED_AT)

    with pytest.raises(ValueError, match="now must be timezone-aware"):
        user.deactivate(now=datetime(2026, 8, 2, 10, 30))


def test_rejects_deactivation_before_creation() -> None:
    user = User.create(now=CREATED_AT)

    with pytest.raises(ValueError, match="deactivated_at must not be before created_at"):
        user.deactivate(now=CREATED_AT - timedelta(microseconds=1))


def test_repeated_deactivation_is_idempotent() -> None:
    user = User.create(now=CREATED_AT).deactivate(now=CREATED_AT + timedelta(days=1))

    assert user.deactivate(now=CREATED_AT + timedelta(days=2)) is user


def test_update_preferences_applies_every_provided_field() -> None:
    user = User.create(now=CREATED_AT)

    updated = user.update_preferences(
        timezone="Europe/Warsaw",
        default_currency="EUR",
        ui_language="RU",
        locale="ru-RU",
    )

    assert updated.timezone == Timezone("Europe/Warsaw")
    assert updated.default_currency == CurrencyCode("EUR")
    assert updated.ui_language == UILanguage("ru")
    assert updated.locale == Locale("ru-RU")


def test_update_preferences_leaves_unprovided_fields_unchanged() -> None:
    user = User.create(now=CREATED_AT).update_preferences(
        timezone="Europe/Warsaw", default_currency="EUR", locale="ru-RU"
    )

    updated = user.update_preferences(ui_language="ru")

    assert updated.timezone == user.timezone
    assert updated.default_currency == user.default_currency
    assert updated.locale == user.locale
    assert updated.ui_language == UILanguage("ru")


def test_update_preferences_can_explicitly_clear_optional_fields() -> None:
    user = User.create(now=CREATED_AT).update_preferences(default_currency="EUR", locale="ru-RU")

    cleared = user.update_preferences(default_currency=None, locale=None)

    assert cleared.default_currency is None
    assert cleared.locale is None


def test_update_preferences_rejects_invalid_value_without_partial_application() -> None:
    user = User.create(now=CREATED_AT)

    with pytest.raises(ValueError, match="unknown IANA timezone"):
        user.update_preferences(timezone="Not/AZone", ui_language="ru")

    # the well-formed ui_language must not have been applied either
    assert user.ui_language == UILanguage("en")


def test_update_preferences_rejects_invalid_currency_without_partial_application() -> None:
    user = User.create(now=CREATED_AT)

    with pytest.raises(ValueError, match="unsupported currency code"):
        user.update_preferences(timezone="Europe/Warsaw", default_currency="XXX")

    assert user.timezone == Timezone("UTC")


@pytest.mark.parametrize(
    ("status", "deactivated_at"),
    [
        (UserStatus.ACTIVE, CREATED_AT),
        (UserStatus.DEACTIVATED, None),
    ],
)
def test_rejects_inconsistent_status_and_deactivated_at(
    status: UserStatus,
    deactivated_at: datetime | None,
) -> None:
    with pytest.raises(ValueError, match="status and deactivated_at are inconsistent"):
        User(
            id=uuid4(),
            status=status,
            created_at=CREATED_AT,
            deactivated_at=deactivated_at,
            timezone=Timezone("UTC"),
            default_currency=None,
            ui_language=UILanguage("en"),
            locale=None,
        )
