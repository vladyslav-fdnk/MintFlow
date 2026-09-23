"""A thin, typed Telegram Bot API client (docs/telegram_client_design.md, T3).

Only the methods the MVP uses. The bot token is part of every request URL, so
transport errors are re-raised without their original exception (which would
carry the URL), and nothing here logs request bodies or message text.
"""

from dataclasses import dataclass
from typing import Final, Protocol

import httpx
from pydantic import SecretStr

from mintflow.telegram.updates import TelegramUpdate

API_BASE_URL: Final = "https://api.telegram.org"
MAX_CALLBACK_DATA_BYTES: Final = 64
MAX_MESSAGE_LENGTH: Final = 4096


class TelegramApiError(Exception):
    """A Bot API call failed. Carries the method name only, never the token or payload."""

    def __init__(self, method: str, *, error_code: int | None = None) -> None:
        super().__init__(f"Telegram Bot API call {method} failed")
        self.method = method
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class InlineButton:
    text: str
    callback_data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("an inline button needs exactly one of callback_data or url")
        if (
            self.callback_data is not None
            and len(self.callback_data.encode()) > MAX_CALLBACK_DATA_BYTES
        ):
            raise ValueError(f"callback_data must be at most {MAX_CALLBACK_DATA_BYTES} bytes")


type InlineKeyboard = tuple[tuple[InlineButton, ...], ...]


@dataclass(frozen=True, slots=True)
class SentMessage:
    chat_id: int
    message_id: int


class TelegramBotApi(Protocol):
    def send_message(
        self, *, chat_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> SentMessage: ...

    def edit_message_text(
        self, *, chat_id: int, message_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> None: ...

    def answer_callback_query(self, *, callback_query_id: str, text: str | None = None) -> None: ...

    def set_webhook(self, *, url: str, secret_token: SecretStr) -> None: ...

    def delete_webhook(self) -> None: ...

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[TelegramUpdate]: ...


def _keyboard_json(keyboard: InlineKeyboard) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [
                {"text": button.text, "callback_data": button.callback_data}
                if button.callback_data is not None
                else {"text": button.text, "url": button.url}
                for button in row
            ]
            for row in keyboard
        ]
    }


def _check_text(text: str) -> None:
    if not text or len(text) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"message text must be 1-{MAX_MESSAGE_LENGTH} characters")


class HttpxTelegramBotApi:
    def __init__(
        self,
        *,
        token: SecretStr,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def __repr__(self) -> str:
        return "HttpxTelegramBotApi(token=SecretStr('**********'))"

    def _call(
        self, method: str, payload: dict[str, object], *, timeout: float | None = None
    ) -> object:
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/{method}"
        try:
            response = self._client.post(
                url,
                json=payload,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            # Suppress the original exception: it carries the URL, and with it the token.
            raise TelegramApiError(method) from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            error_code = body.get("error_code") if isinstance(body, dict) else None
            raise TelegramApiError(
                method, error_code=error_code if isinstance(error_code, int) else None
            )
        return body.get("result")

    def send_message(
        self, *, chat_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> SentMessage:
        _check_text(text)
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if keyboard is not None:
            payload["reply_markup"] = _keyboard_json(keyboard)
        result = self._call("sendMessage", payload)
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramApiError("sendMessage")
        return SentMessage(chat_id=chat_id, message_id=result["message_id"])

    def edit_message_text(
        self, *, chat_id: int, message_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> None:
        _check_text(text)
        payload: dict[str, object] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if keyboard is not None:
            payload["reply_markup"] = _keyboard_json(keyboard)
        self._call("editMessageText", payload)

    def answer_callback_query(self, *, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, object] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)

    def set_webhook(self, *, url: str, secret_token: SecretStr) -> None:
        self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token.get_secret_value(),
                "allowed_updates": ["message", "callback_query"],
            },
        )

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", {})

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[TelegramUpdate]:
        payload: dict[str, object] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        # Long polling holds the request open for timeout_seconds; allow a margin on top.
        result = self._call("getUpdates", payload, timeout=timeout_seconds + 10)
        if not isinstance(result, list):
            raise TelegramApiError("getUpdates")
        updates = []
        for raw in result:
            try:
                updates.append(TelegramUpdate.model_validate(raw))
            except ValueError:
                # Keep the id of an update we cannot read, so the polling offset still
                # advances past it; the rest of it is ignored.
                update_id = raw.get("update_id") if isinstance(raw, dict) else None
                if isinstance(update_id, int):
                    updates.append(TelegramUpdate(update_id=update_id))
        return updates
