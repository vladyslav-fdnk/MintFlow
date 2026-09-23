"""An in-memory Telegram Bot API for tests. Never used by the running application."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count

from pydantic import SecretStr

from mintflow.telegram.bot_api import InlineKeyboard, SentMessage, TelegramApiError
from mintflow.telegram.updates import TelegramUpdate


@dataclass
class RecordedCall:
    method: str
    arguments: dict[str, object]


@dataclass
class RecordingTelegramBotApi:
    """Records every call; ``fail_methods`` makes the named methods raise TelegramApiError."""

    calls: list[RecordedCall] = field(default_factory=list)
    pending_updates: list[TelegramUpdate] = field(default_factory=list)
    fail_methods: set[str] = field(default_factory=set)
    _message_ids: Iterator[int] = field(default_factory=lambda: count(1))

    def _record(self, method: str, **arguments: object) -> None:
        self.calls.append(RecordedCall(method=method, arguments=arguments))
        if method in self.fail_methods:
            raise TelegramApiError(method)

    def calls_to(self, method: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.method == method]

    def send_message(
        self, *, chat_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> SentMessage:
        self._record("send_message", chat_id=chat_id, text=text, keyboard=keyboard)
        return SentMessage(chat_id=chat_id, message_id=next(self._message_ids))

    def edit_message_text(
        self, *, chat_id: int, message_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> None:
        self._record(
            "edit_message_text",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            keyboard=keyboard,
        )

    def answer_callback_query(self, *, callback_query_id: str, text: str | None = None) -> None:
        self._record("answer_callback_query", callback_query_id=callback_query_id, text=text)

    def set_webhook(self, *, url: str, secret_token: SecretStr) -> None:
        self._record("set_webhook", url=url)

    def delete_webhook(self) -> None:
        self._record("delete_webhook")

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[TelegramUpdate]:
        self._record("get_updates", offset=offset, timeout_seconds=timeout_seconds)
        ready = [
            update
            for update in self.pending_updates
            if offset is None or update.update_id >= offset
        ]
        self.pending_updates = [update for update in self.pending_updates if update not in ready]
        return ready
