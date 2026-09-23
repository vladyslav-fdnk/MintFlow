"""The subset of Telegram updates MintFlow reads (docs/telegram_client_design.md, T3).

Only the verified numeric sender id is identity; names and usernames are
display data. Anything MintFlow does not handle parses as an ignorable update
instead of failing.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_START_COMMAND = "/start"


class _TelegramModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class TelegramUser(_TelegramModel):
    id: int
    is_bot: bool = False
    first_name: str = ""
    username: str | None = None


class TelegramChat(_TelegramModel):
    id: int
    type: str


class TelegramPhotoSize(_TelegramModel):
    file_id: str
    width: int = 0
    height: int = 0
    file_size: int | None = None


class TelegramDocument(_TelegramModel):
    file_id: str
    mime_type: str | None = None
    file_size: int | None = None


class TelegramMessage(_TelegramModel):
    message_id: int
    sender: TelegramUser | None = Field(default=None, alias="from")
    chat: TelegramChat
    text: str | None = None
    photo: tuple[TelegramPhotoSize, ...] | None = None
    document: TelegramDocument | None = None
    # Set when the message is one item of an album.
    media_group_id: str | None = None
    # Present on forwarded messages (Bot API 7.0+ and the legacy fields).
    forward_origin: dict[str, object] | None = None
    forward_date: int | None = None

    @property
    def is_forwarded(self) -> bool:
        return self.forward_origin is not None or self.forward_date is not None


class TelegramCallbackQuery(_TelegramModel):
    id: str
    sender: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class UpdateKind(StrEnum):
    TEXT_MESSAGE = "text_message"
    MEDIA_MESSAGE = "media_message"
    CALLBACK = "callback"
    IGNORABLE = "ignorable"


class TelegramUpdate(_TelegramModel):
    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None

    @property
    def kind(self) -> UpdateKind:
        if self.callback_query is not None and self.callback_query.data is not None:
            return UpdateKind.CALLBACK
        message = self.message
        if message is None or message.sender is None or message.sender.is_bot:
            return UpdateKind.IGNORABLE
        if message.text is not None:
            return UpdateKind.TEXT_MESSAGE
        if message.photo or message.document is not None:
            return UpdateKind.MEDIA_MESSAGE
        return UpdateKind.IGNORABLE

    @property
    def sender(self) -> TelegramUser | None:
        if self.callback_query is not None:
            return self.callback_query.sender
        return self.message.sender if self.message is not None else None

    @property
    def chat(self) -> TelegramChat | None:
        if self.callback_query is not None:
            message = self.callback_query.message
            return message.chat if message is not None else None
        return self.message.chat if self.message is not None else None

    @property
    def is_private_chat(self) -> bool:
        """True only for a one-to-one chat with the user who sent the update."""
        chat, sender = self.chat, self.sender
        return (
            chat is not None
            and sender is not None
            and chat.type == "private"
            and chat.id == sender.id
        )

    @property
    def start_payload(self) -> str | None:
        """The deep-link payload of ``/start <payload>`` in a typed, non-forwarded message."""
        message = self.message
        if message is None or message.text is None or message.is_forwarded:
            return None
        command, _, payload = message.text.strip().partition(" ")
        if command != _START_COMMAND:
            return None
        payload = payload.strip()
        return payload or None


def parse_update(raw: bytes | str) -> TelegramUpdate | None:
    """Parse one webhook or getUpdates payload; None when it is not a usable update."""
    try:
        return TelegramUpdate.model_validate_json(raw)
    except ValidationError:
        return None
