"""Background receipt processing (receipt_recognition_design.md, R2, R6, R7).

One ``process_next`` call claims a receipt, obtains its image (stored copy first,
Telegram otherwise), recognizes it within a time limit, and then, in one
transaction, finishes the attempt, stores the result, applies it to the draft,
and moves the user's conversation on. Only after that commit does the user get
the review card or a question. A stale attempt (another worker took over after
the lease expired) changes nothing and sends nothing.

A receipt still unread 30 seconds after it arrived gets one "taking longer"
message with an "Enter manually" button (R7). The worker looks for such receipts
before each claim and about once a second while a recognizer is running. A
result that arrives after the user continued manually fills only untouched
fields; the flow decides whether to show the updated review card.

Locks are taken in the same order as the capture flow: conversation, then draft.
Logs carry outcome categories and durations only, never receipt contents.
"""

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from mintflow.application.receipts import (
    MAX_RECEIPT_IMAGE_BYTES,
    ReceiptRecognizer,
    RecognitionOutput,
    RecognitionUnavailable,
    select_values,
    sniff_media_type,
)
from mintflow.application.telegram import (
    AwaitingInput,
    TelegramConversation,
    TelegramConversationRepository,
)
from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureDraftState,
    Receipt,
    RecognitionResult,
)
from mintflow.domain.user import User
from mintflow.infrastructure.persistence import StoredImage
from mintflow.telegram import messages
from mintflow.telegram.bot_api import TelegramApiError, TelegramBotApi
from mintflow.telegram.capture_flow import ManualCaptureFlow
from mintflow.telegram.outgoing import Outgoing, Reply

RECEIPT_LEASE: Final = timedelta(minutes=2)
RECOGNITION_TIMEOUT_SECONDS: Final = 60.0
DELAY_NOTICE_AFTER: Final = timedelta(seconds=30)
# How often a running recognition is interrupted to look for receipts that need a delay notice.
WAIT_SLICE_SECONDS: Final = 1.0

logger = logging.getLogger("mintflow.telegram.receipt_worker")


class ReceiptOutcome(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_AMOUNT = "needs_amount"
    FAILED = "failed"
    # The user had already continued manually; the result filled only untouched fields.
    LATE_RESULT = "late_result"
    DRAFT_CLOSED = "draft_closed"
    STALE = "stale"


class ReceiptRepository(Protocol):
    def claim_next(self, *, now: datetime, lease: timedelta) -> Receipt | None: ...

    def telegram_file_id(self, *, receipt_id: UUID) -> str | None: ...

    def claim_delay_notices(
        self, *, now: datetime, received_before: datetime
    ) -> list[tuple[UUID, UUID]]: ...

    def save_finished(self, receipt: Receipt) -> bool: ...

    def save_result(self, result: RecognitionResult) -> None: ...


class ImageStore(Protocol):
    def put(self, *, receipt_id: UUID, media_type: str, content: bytes, now: datetime) -> bool:
        """False when the receipt no longer exists (its account was deleted)."""
        ...

    def get(self, *, receipt_id: UUID) -> StoredImage | None: ...


class ReceiptDraftRepository(Protocol):
    def get_for_update_by_receipt(self, *, receipt_id: UUID) -> CaptureDraft | None: ...

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None: ...


class UserRepository(Protocol):
    def get(self, user_id: UUID) -> User | None: ...


class ChatResolver(Protocol):
    def chat_for(self, *, user_id: UUID) -> int | None:
        """The private chat of the user's active Telegram connection, if any."""
        ...


class Transaction(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Image:
    media_type: str
    content: bytes


class _Vanished(Exception):
    """The receipt was deleted with its account while being processed."""


class _Unreadable(Exception):
    """The image could not be obtained or is not an accepted image."""


class ReceiptWorker:
    def __init__(
        self,
        *,
        receipts: ReceiptRepository,
        images: ImageStore,
        drafts: ReceiptDraftRepository,
        conversations: TelegramConversationRepository,
        users: UserRepository,
        chats: ChatResolver,
        transaction: Transaction,
        recognizer: ReceiptRecognizer,
        bot_api: TelegramBotApi,
        flow: ManualCaptureFlow,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        recognition_timeout_seconds: float = RECOGNITION_TIMEOUT_SECONDS,
        wait_slice_seconds: float = WAIT_SLICE_SECONDS,
    ) -> None:
        self._receipts = receipts
        self._images = images
        self._drafts = drafts
        self._conversations = conversations
        self._users = users
        self._chats = chats
        self._transaction = transaction
        self._recognizer = recognizer
        self._bot_api = bot_api
        self._flow = flow
        self._clock = clock
        self._timeout = recognition_timeout_seconds
        self._wait_slice = wait_slice_seconds

    def process_next(self) -> ReceiptOutcome | None:
        """Process one receipt; None when the queue is empty."""
        self.send_delay_notices()
        receipt = self._receipts.claim_next(now=self._clock(), lease=RECEIPT_LEASE)
        if receipt is None or receipt.attempt_id is None:
            return None
        started = time.monotonic()
        try:
            image = self._image_for(receipt)
            output: RecognitionOutput | None = self._recognize(image)
        except _Unreadable:
            output = None
        except _Vanished:
            logger.info("receipt_processed outcome=%s", ReceiptOutcome.STALE.value)
            return ReceiptOutcome.STALE
        outcome = self._finish(receipt, receipt.attempt_id, output=output)
        logger.info(
            "receipt_processed outcome=%s duration_ms=%d",
            outcome.value,
            int((time.monotonic() - started) * 1000),
        )
        return outcome

    def send_delay_notices(self) -> int:
        """Send the "taking longer" message for overdue receipts; returns how many were sent.

        The claim is committed before anything is sent, so a receipt never gets the message
        twice, even if sending fails. It is sent only while the receipt's draft is the
        user's active draft and still waiting for recognition.
        """
        now = self._clock()
        notices: list[Reply] = []
        try:
            claimed = self._receipts.claim_delay_notices(
                now=now, received_before=now - DELAY_NOTICE_AFTER
            )
            for receipt_id, owner_id in claimed:
                conversation = self._conversations.lock(user_id=owner_id, now=now)
                draft = self._drafts.get_for_update_by_receipt(receipt_id=receipt_id)
                if (
                    draft is None
                    or draft.state is not CaptureDraftState.AWAITING_RECOGNITION
                    or conversation.active_draft_id != draft.id
                ):
                    continue
                chat_id = self._chats.chat_for(user_id=owner_id)
                if chat_id is not None:
                    notices.append(self._flow.delay_notice(chat_id, draft))
            self._transaction.commit()
        except BaseException:
            self._transaction.rollback()
            raise
        self._send(notices)
        return len(notices)

    # --- steps -------------------------------------------------------------------------------

    def _image_for(self, receipt: Receipt) -> _Image:
        stored = self._images.get(receipt_id=receipt.id)
        if stored is not None:
            return _Image(stored.media_type, stored.content)
        file_id = self._receipts.telegram_file_id(receipt_id=receipt.id)
        if file_id is None:
            raise _Unreadable
        try:
            telegram_file = self._bot_api.get_file(file_id=file_id)
            content = self._bot_api.download_file(
                file_path=telegram_file.file_path, max_bytes=MAX_RECEIPT_IMAGE_BYTES
            )
        except TelegramApiError:
            raise _Unreadable from None
        media_type = sniff_media_type(content)
        if media_type is None:
            raise _Unreadable
        # Keep the image even if recognition fails, per the retention policy (design R4).
        # Committed now: delay notices commit while recognition runs, and a worker that takes
        # over after the lease expires should not download the file again.
        try:
            kept = self._images.put(
                receipt_id=receipt.id, media_type=media_type, content=content, now=self._clock()
            )
            self._transaction.commit()
        except BaseException:
            self._transaction.rollback()
            raise
        if not kept:
            raise _Vanished
        return _Image(media_type, content)

    def _recognize(self, image: _Image) -> RecognitionOutput | None:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            self._recognizer.recognize, image=image.content, media_type=image.media_type
        )
        deadline = time.monotonic() + self._timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    return future.result(timeout=min(remaining, self._wait_slice))
                except FutureTimeout:
                    if future.done():
                        # The recognizer itself raised TimeoutError (the same class since 3.11).
                        raise
                    self._send_delay_notices_while_waiting()
        except RecognitionUnavailable:
            return None
        except Exception as error:
            # A faulty adapter must not stop the worker; the receipt falls back to manual entry.
            logger.error("receipt_recognizer_error error=%s", type(error).__name__)
            return None
        finally:
            # Never wait for a hung recognizer; its eventual result is simply dropped.
            executor.shutdown(wait=False, cancel_futures=True)

    def _send_delay_notices_while_waiting(self) -> None:
        try:
            self.send_delay_notices()
        except Exception as error:
            # The receipt being recognized must not fail because a notice could not be sent.
            logger.error("receipt_delay_notices_failed error=%s", type(error).__name__)

    def _finish(
        self, receipt: Receipt, attempt_id: UUID, *, output: RecognitionOutput | None
    ) -> ReceiptOutcome:
        now = self._clock()
        try:
            user = self._users.get(receipt.owner_id)
            finished = (
                receipt.fail(attempt_id=attempt_id, now=now)
                if output is None or user is None
                else receipt.complete(attempt_id=attempt_id, now=now)
            )
            if not self._receipts.save_finished(finished):
                self._transaction.rollback()
                return ReceiptOutcome.STALE
            conversation = self._conversations.lock(user_id=receipt.owner_id, now=now)
            draft = self._drafts.get_for_update_by_receipt(receipt_id=receipt.id)
            open_states = (
                CaptureDraftState.AWAITING_RECOGNITION,
                CaptureDraftState.COLLECTING,
                CaptureDraftState.READY_FOR_REVIEW,
            )
            result = None
            if output is not None and user is not None:
                values = select_values(output, now=now, timezone=user.timezone)
                result = RecognitionResult.for_attempt(
                    receipt=receipt,
                    attempt_id=attempt_id,
                    merchant=values.merchant,
                    transaction_date=values.transaction_date,
                    total=values.total,
                    now=now,
                )
                self._receipts.save_result(result)
            if draft is None or draft.state not in open_states:
                self._transaction.commit()
                return ReceiptOutcome.DRAFT_CLOSED

            was_waiting = draft.state is CaptureDraftState.AWAITING_RECOGNITION
            before = draft
            if result is not None:
                draft = draft.apply_recognition(
                    result=result, fallback_category_key=UNCATEGORIZED_KEY, now=now
                )
            else:
                draft = draft.continue_manually(caller_id=draft.owner_id, now=now)
            self._drafts.update(draft, commit=False)
            is_active = conversation.active_draft_id == draft.id
            late_replies: list[Outgoing] = []
            if result is None:
                outcome = ReceiptOutcome.FAILED
            elif not was_waiting:
                outcome = ReceiptOutcome.LATE_RESULT
                chat_id = self._chats.chat_for(user_id=receipt.owner_id) if user else None
                if user is not None and chat_id is not None:
                    late_replies = self._flow.after_late_result(
                        user, chat_id, conversation, before, draft
                    )
            elif draft.state is CaptureDraftState.READY_FOR_REVIEW:
                outcome = ReceiptOutcome.READY_FOR_REVIEW
            else:
                outcome = ReceiptOutcome.NEEDS_AMOUNT
            if is_active and was_waiting and outcome is not ReceiptOutcome.READY_FOR_REVIEW:
                conversation = conversation.waiting_for(AwaitingInput.AMOUNT, now=now)
                self._conversations.save(conversation)
            self._transaction.commit()
        except BaseException:
            self._transaction.rollback()
            raise

        if is_active and was_waiting:
            self._notify(receipt.owner_id, outcome, conversation, draft)
        self._send(late_replies)
        return outcome

    def _notify(
        self,
        owner_id: UUID,
        outcome: ReceiptOutcome,
        conversation: TelegramConversation,
        draft: CaptureDraft,
    ) -> None:
        chat_id = self._chats.chat_for(user_id=owner_id)
        if chat_id is None:
            return
        try:
            if outcome is ReceiptOutcome.READY_FOR_REVIEW:
                card = self._flow.review_reply(chat_id, conversation, draft)
                self._bot_api.send_message(chat_id=chat_id, text=card.text, keyboard=card.keyboard)
            elif outcome is ReceiptOutcome.NEEDS_AMOUNT:
                self._bot_api.send_message(chat_id=chat_id, text=messages.RECEIPT_NEEDS_AMOUNT)
            else:
                self._bot_api.send_message(chat_id=chat_id, text=messages.RECEIPT_FAILED)
        except TelegramApiError as error:
            logger.warning(
                "receipt_notification_failed method=%s code=%s", error.method, error.error_code
            )

    def _send(self, replies: Sequence[Outgoing]) -> None:
        for reply in replies:
            if not isinstance(reply, Reply):
                continue
            try:
                self._bot_api.send_message(
                    chat_id=reply.chat_id, text=reply.text, keyboard=reply.keyboard
                )
            except TelegramApiError as error:
                logger.warning(
                    "receipt_notification_failed method=%s code=%s",
                    error.method,
                    error.error_code,
                )
