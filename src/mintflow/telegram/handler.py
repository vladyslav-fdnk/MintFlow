"""Telegram update handling: deduplication, dispatch, and replies after commit.

One entry point, ``TelegramUpdateHandler.handle``, serves both the webhook and
local long polling (docs/telegram_client_design.md, T2, T4). The update's
``update_id`` and every database change it causes commit together; replies are
sent only after that commit, and a failed send is logged without its content.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from mintflow.application.telegram import telegram_display_name
from mintflow.domain.user import User
from mintflow.telegram import messages
from mintflow.telegram.bot_api import TelegramApiError, TelegramBotApi
from mintflow.telegram.outgoing import CallbackAnswer, EditMessage, Outgoing, Reply
from mintflow.telegram.updates import TelegramUpdate, UpdateKind

logger = logging.getLogger("mintflow.telegram.handler")


class UserResolver(Protocol):
    def execute(self, *, telegram_user_id: int) -> User | None: ...


class LinkClaimer(Protocol):
    def execute(self, *, payload: str, telegram_user_id: int, display_name: str | None) -> bool: ...


class LinkedCapture(Protocol):
    """What linked users can do beyond linking and help: capture (TG-06) and history (TG-07)."""

    def on_command(self, user: User, chat_id: int, command: str) -> list[Outgoing] | None: ...

    def on_text(self, user: User, chat_id: int, text: str) -> list[Outgoing]: ...

    def on_callback(
        self, user: User, chat_id: int, message_id: int, callback_query_id: str, data: str
    ) -> list[Outgoing] | None: ...


class UpdateLedger(Protocol):
    def begin(self, *, update_id: int, now: datetime) -> bool: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def _command(text: str) -> str:
    """``/help@mintflow_bot extra`` -> ``/help``."""
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", 1)[0].lower()


class TelegramUpdateHandler:
    def __init__(
        self,
        *,
        ledger: UpdateLedger,
        resolve_user: UserResolver,
        claim_link: LinkClaimer,
        capture: LinkedCapture,
        bot_api: TelegramBotApi,
        web_origin: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ledger = ledger
        self._resolve_user = resolve_user
        self._claim_link = claim_link
        self._capture = capture
        self._bot_api = bot_api
        self._web_origin = web_origin
        self._clock = clock

    def handle(self, update: TelegramUpdate) -> None:
        if update.kind is UpdateKind.IGNORABLE or not update.is_private_chat:
            # Group, channel, bot, and unsupported updates never touch MintFlow data.
            return
        try:
            if not self._ledger.begin(update_id=update.update_id, now=self._clock()):
                self._ledger.rollback()
                return
            outgoing = self._dispatch(update)
            self._ledger.commit()
        except BaseException:
            self._ledger.rollback()
            raise
        self._send(outgoing)

    def _dispatch(self, update: TelegramUpdate) -> list[Outgoing]:
        sender, chat = update.sender, update.chat
        if sender is None or chat is None:
            return []
        callback = update.callback_query
        if update.kind is UpdateKind.CALLBACK and callback is not None:
            user = self._resolve_user.execute(telegram_user_id=sender.id)
            message_id = callback.message.message_id if callback.message is not None else None
            return self._on_callback(user, chat.id, message_id, callback.id, callback.data or "")

        message = update.message
        if message is None or message.text is None:
            return []
        payload = update.start_payload
        if payload is not None:
            claimed = self._claim_link.execute(
                payload=payload,
                telegram_user_id=sender.id,
                display_name=telegram_display_name(
                    first_name=sender.first_name, username=sender.username
                ),
            )
            return [Reply(chat.id, messages.LINK_CLAIMED if claimed else messages.LINK_FAILED)]

        command = _command(message.text)
        if command == "/help":
            return [Reply(chat.id, messages.help_text(web_origin=self._web_origin))]
        user = self._resolve_user.execute(telegram_user_id=sender.id)
        if command == "/start":
            return [Reply(chat.id, messages.WELCOME_LINKED if user else messages.WELCOME_UNLINKED)]
        if user is None:
            return [Reply(chat.id, messages.NOT_LINKED)]
        return self._on_linked_text(user, chat.id, message.text)

    def _on_linked_text(self, user: User, chat_id: int, text: str) -> list[Outgoing]:
        if text.strip().startswith("/"):
            handled = self._capture.on_command(user, chat_id, _command(text))
            return handled if handled is not None else [Reply(chat_id, messages.UNKNOWN_INPUT)]
        return self._capture.on_text(user, chat_id, text)

    def _on_callback(
        self,
        user: User | None,
        chat_id: int,
        message_id: int | None,
        callback_query_id: str,
        data: str,
    ) -> list[Outgoing]:
        """Button presses. Every callback is answered so Telegram stops its spinner."""
        if user is None:
            return [CallbackAnswer(callback_query_id), Reply(chat_id, messages.NOT_LINKED)]
        if message_id is not None:
            handled = self._capture.on_callback(user, chat_id, message_id, callback_query_id, data)
            if handled is not None:
                return handled
        return [CallbackAnswer(callback_query_id, messages.UNKNOWN_INPUT)]

    def _send(self, outgoing: list[Outgoing]) -> None:
        for item in outgoing:
            try:
                if isinstance(item, Reply):
                    self._bot_api.send_message(
                        chat_id=item.chat_id, text=item.text, keyboard=item.keyboard
                    )
                elif isinstance(item, EditMessage):
                    self._bot_api.edit_message_text(
                        chat_id=item.chat_id,
                        message_id=item.message_id,
                        text=item.text,
                        keyboard=item.keyboard,
                    )
                else:
                    self._bot_api.answer_callback_query(
                        callback_query_id=item.callback_query_id, text=item.text
                    )
            except TelegramApiError as error:
                logger.warning(
                    "telegram_reply_failed method=%s code=%s", error.method, error.error_code
                )
