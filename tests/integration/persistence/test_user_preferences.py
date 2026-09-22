from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from mintflow.domain.capture import CurrencyCode
from mintflow.domain.user import Locale, Timezone, UILanguage, User
from mintflow.infrastructure.persistence import SqlAlchemyUserRepository
from mintflow.infrastructure.persistence.models import UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def test_persists_and_reloads_a_user_with_preferences_set(db_session: Session) -> None:
    repository = SqlAlchemyUserRepository(db_session)
    user = User.create(
        now=NOW,
        timezone=Timezone("Europe/Warsaw"),
        default_currency=CurrencyCode("EUR"),
        ui_language=UILanguage("ru"),
        locale=Locale("ru-RU"),
    )
    db_session.add(_record(user))
    db_session.commit()

    fetched = repository.get(user.id)

    assert fetched == user


def test_persists_and_reloads_a_user_with_default_preferences(db_session: Session) -> None:
    repository = SqlAlchemyUserRepository(db_session)
    user = User.create(now=NOW)
    db_session.add(_record(user))
    db_session.commit()

    fetched = repository.get(user.id)

    assert fetched == user
    assert fetched is not None
    assert fetched.timezone == Timezone("UTC")
    assert fetched.ui_language == UILanguage("en")
    assert fetched.default_currency is None
    assert fetched.locale is None


def test_update_preferences_persists_the_new_values(db_session: Session) -> None:
    repository = SqlAlchemyUserRepository(db_session)
    user = User.create(now=NOW)
    db_session.add(_record(user))
    db_session.commit()

    updated = user.update_preferences(
        timezone="Europe/Warsaw", default_currency="EUR", ui_language="ru", locale="ru-RU"
    )
    repository.update_preferences(updated)

    fetched = repository.get(user.id)
    assert fetched == updated


def test_get_returns_none_for_an_unknown_user(db_session: Session) -> None:
    repository = SqlAlchemyUserRepository(db_session)

    assert repository.get(uuid4()) is None


def _record(user: User) -> UserRecord:
    return UserRecord(
        id=user.id,
        status=user.status.value,
        created_at=user.created_at,
        deactivated_at=user.deactivated_at,
        timezone=user.timezone.value,
        default_currency=(
            user.default_currency.value if user.default_currency is not None else None
        ),
        ui_language=user.ui_language.value,
        locale=user.locale.value if user.locale is not None else None,
    )
