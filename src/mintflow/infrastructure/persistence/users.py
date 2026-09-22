from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from mintflow.domain.capture import CurrencyCode
from mintflow.domain.user import Locale, Timezone, UILanguage, User, UserStatus
from mintflow.infrastructure.persistence.models import UserRecord


def _to_domain(record: UserRecord) -> User:
    return User(
        id=record.id,
        status=UserStatus(record.status),
        created_at=record.created_at,
        deactivated_at=record.deactivated_at,
        timezone=Timezone(record.timezone),
        default_currency=(
            CurrencyCode(record.default_currency) if record.default_currency is not None else None
        ),
        ui_language=UILanguage(record.ui_language),
        locale=Locale(record.locale) if record.locale is not None else None,
    )


class SqlAlchemyUserRepository:
    """Reads and persists User preferences only.

    User creation and authentication state remain owned by the
    user-foundation sprint's own code paths (login_challenges.py); this
    repository must not be used to create or authenticate a User.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: UUID) -> User | None:
        record = self._session.get(UserRecord, user_id)
        return _to_domain(record) if record is not None else None

    def update_preferences(self, user: User) -> None:
        self._session.execute(
            update(UserRecord)
            .where(UserRecord.id == user.id)
            .values(
                timezone=user.timezone.value,
                default_currency=(
                    user.default_currency.value if user.default_currency is not None else None
                ),
                ui_language=user.ui_language.value,
                locale=user.locale.value if user.locale is not None else None,
            )
        )
        self._session.commit()
