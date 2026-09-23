from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mintflow.infrastructure.persistence.models import TelegramProcessedUpdateRecord


class SqlAlchemyTelegramUpdateLedger:
    """Deduplicates Telegram updates by ``update_id`` (docs/telegram_client_design.md, T4).

    ``begin`` inserts the id without committing, so the id and the work the update
    causes commit or roll back together. A concurrent redelivery blocks on the
    primary key until the first transaction ends, then sees the conflict.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def begin(self, *, update_id: int, now: datetime) -> bool:
        """True when this update has not been processed before."""
        inserted = self._session.scalar(
            insert(TelegramProcessedUpdateRecord)
            .values(update_id=update_id, processed_at=now)
            .on_conflict_do_nothing(index_elements=["update_id"])
            .returning(TelegramProcessedUpdateRecord.update_id)
        )
        return inserted is not None

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
