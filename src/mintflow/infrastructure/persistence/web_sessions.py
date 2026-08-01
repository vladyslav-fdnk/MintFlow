from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication.web_session import AuthenticatedWebSession
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord


class SqlAlchemyWebSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def authenticate(self, *, secret_hash: bytes, now: datetime) -> AuthenticatedWebSession | None:
        row = self._session.execute(
            select(WebSessionRecord.id, WebSessionRecord.user_id)
            .join(UserRecord, WebSessionRecord.user_id == UserRecord.id)
            .where(
                WebSessionRecord.secret_hash == secret_hash,
                WebSessionRecord.revoked_at.is_(None),
                WebSessionRecord.expires_at > now,
                UserRecord.status == UserStatus.ACTIVE.value,
            )
        ).one_or_none()
        if row is None:
            return None
        return AuthenticatedWebSession(session_id=row.id, user_id=row.user_id)

    def revoke(self, *, session_id: UUID, revoked_at: datetime) -> bool:
        with self._session.begin():
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(WebSessionRecord)
                    .where(
                        WebSessionRecord.id == session_id,
                        WebSessionRecord.revoked_at.is_(None),
                    )
                    .values(revoked_at=revoked_at)
                ),
            )
        return result.rowcount == 1

    def revoke_all(self, *, user_id: UUID, revoked_at: datetime) -> int:
        with self._session.begin():
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(WebSessionRecord)
                    .where(
                        WebSessionRecord.user_id == user_id,
                        WebSessionRecord.revoked_at.is_(None),
                    )
                    .values(revoked_at=revoked_at)
                ),
            )
        return result.rowcount
