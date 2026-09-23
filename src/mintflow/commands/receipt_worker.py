"""Process queued receipts: ``python -m mintflow.commands.receipt_worker``.

Runs until interrupted, one receipt at a time; several workers may run at once
because receipts are claimed with SKIP LOCKED. Each iteration also sends the
"taking longer" message for receipts unread after 30 seconds. Needs Telegram and a receipt
recognizer configured (MINTFLOW_RECEIPT_RECOGNIZER; only "fake" exists until a
provider is chosen in RCPT-07).
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from mintflow.application.capture import ConfirmCaptureDraft
from mintflow.application.receipts import ReceiptRecognizer
from mintflow.config import Settings, get_settings
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyReceiptImageStore,
    SqlAlchemyReceiptRepository,
    SqlAlchemyTelegramConversationRepository,
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyUserRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.recognition.fake import FakeReceiptRecognizer
from mintflow.logging import configure_logging
from mintflow.telegram.capture_flow import ManualCaptureFlow
from mintflow.telegram.receipt_worker import ReceiptOutcome, ReceiptWorker
from mintflow.telegram.runtime import TelegramRuntime, build_telegram_runtime

IDLE_SECONDS: Final = 1.0

logger = logging.getLogger("mintflow.commands.receipt_worker")


class _LinkedChats:
    def __init__(self, links: SqlAlchemyTelegramLinkRepository) -> None:
        self._links = links

    def chat_for(self, *, user_id: UUID) -> int | None:
        connection = self._links.active_connection_for_user(user_id=user_id)
        # In a private chat the chat id is the Telegram user id.
        return connection.telegram_user_id if connection is not None else None


def build_receipt_worker(
    session: Session, runtime: TelegramRuntime, recognizer: ReceiptRecognizer
) -> ReceiptWorker:
    """The production composition of one worker on one database session."""
    drafts = SqlAlchemyCaptureDraftRepository(session)
    users = SqlAlchemyUserRepository(session)
    conversations = SqlAlchemyTelegramConversationRepository(session)
    expenses = SqlAlchemyExpenseRepository(session)
    receipts = SqlAlchemyReceiptRepository(session)
    flow = ManualCaptureFlow(
        conversations=conversations,
        drafts=drafts,
        categories=SqlAlchemyCategoryRepository(session),
        confirm=ConfirmCaptureDraft(
            draft_repository=drafts,
            expense_repository=expenses,
            user_repository=users,
            clock=runtime.clock,
        ),
        history=expenses,
        receipts=receipts,
        web_origin=runtime.web_origin,
        clock=runtime.clock,
    )
    return ReceiptWorker(
        receipts=receipts,
        images=SqlAlchemyReceiptImageStore(session),
        drafts=drafts,
        conversations=conversations,
        users=users,
        chats=_LinkedChats(SqlAlchemyTelegramLinkRepository(session)),
        transaction=session,
        recognizer=recognizer,
        bot_api=runtime.bot_api,
        flow=flow,
        clock=runtime.clock,
    )


def build_recognizer(settings: Settings) -> ReceiptRecognizer | None:
    if settings.receipt_recognizer == "fake":
        return FakeReceiptRecognizer()
    return None


def run(
    process_next: Callable[[], ReceiptOutcome | None],
    *,
    should_continue: Callable[[], bool] = lambda: True,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Drain the queue, then wait briefly before looking again."""
    while should_continue():
        try:
            outcome = process_next()
        except Exception as error:
            logger.error("receipt_worker_iteration_failed error=%s", type(error).__name__)
            sleep(IDLE_SECONDS)
            continue
        if outcome is None:
            sleep(IDLE_SECONDS)


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="Process queued receipts.").parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    runtime = build_telegram_runtime(settings)
    if runtime is None:
        print("telegram is not configured", file=sys.stderr)
        return 2
    recognizer = build_recognizer(settings)
    if recognizer is None:
        print("no receipt recognizer is configured", file=sys.stderr)
        return 2

    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)

    def process_next() -> ReceiptOutcome | None:
        with session_factory() as session:
            return build_receipt_worker(session, runtime, recognizer).process_next()

    try:
        run(process_next)
    except KeyboardInterrupt:
        return 0
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
