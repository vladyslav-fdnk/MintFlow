from sqlalchemy.orm import Session

from mintflow.application.authentication.audit import AuthenticationAuditRecord
from mintflow.infrastructure.persistence.models import AuthenticationAuditRecordModel


class SqlAlchemyAuthenticationAuditAppender:
    """Append-only persistence interface; cleanup is deliberately a separate future concern."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, record: AuthenticationAuditRecord) -> None:
        with self._session.begin():
            self._session.add(
                AuthenticationAuditRecordModel(
                    id=record.id,
                    occurred_at=record.occurred_at,
                    event_type=record.event_type.value,
                    outcome=record.outcome.value,
                    user_id=record.user_id,
                    subject_record_id=record.subject_record_id,
                )
            )
