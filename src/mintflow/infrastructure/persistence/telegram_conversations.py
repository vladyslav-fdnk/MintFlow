from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mintflow.application.telegram.conversation import AwaitingInput, TelegramConversation
from mintflow.infrastructure.persistence.models import TelegramConversationRecord


class SqlAlchemyTelegramConversationRepository:
    """Neither method commits: conversation changes commit with the update that caused them."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock(self, *, user_id: UUID, now: datetime) -> TelegramConversation:
        """Create the row if missing, then lock it, so a user's updates are serialized
        even before their first conversation exists."""
        self._session.execute(
            insert(TelegramConversationRecord)
            .values(
                user_id=user_id,
                active_draft_id=None,
                awaiting=AwaitingInput.NOTHING.value,
                currency_is_default=False,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        record = self._session.scalars(
            select(TelegramConversationRecord)
            .where(TelegramConversationRecord.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        return TelegramConversation(
            user_id=record.user_id,
            active_draft_id=record.active_draft_id,
            awaiting=AwaitingInput(record.awaiting),
            currency_is_default=record.currency_is_default,
            updated_at=record.updated_at,
            pending_receipt_file_id=record.pending_receipt_file_id,
        )

    def save(self, conversation: TelegramConversation) -> None:
        values = {
            "active_draft_id": conversation.active_draft_id,
            "awaiting": conversation.awaiting.value,
            "currency_is_default": conversation.currency_is_default,
            "updated_at": conversation.updated_at,
            "pending_receipt_file_id": conversation.pending_receipt_file_id,
        }
        self._session.execute(
            insert(TelegramConversationRecord)
            .values(user_id=conversation.user_id, **values)
            .on_conflict_do_update(index_elements=["user_id"], set_=values)
        )
