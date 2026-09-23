"""What a handled update asks the bot to send once its transaction has committed."""

from dataclasses import dataclass

from mintflow.telegram.bot_api import InlineKeyboard


@dataclass(frozen=True, slots=True)
class Reply:
    chat_id: int
    text: str
    keyboard: InlineKeyboard | None = None


@dataclass(frozen=True, slots=True)
class EditMessage:
    chat_id: int
    message_id: int
    text: str
    keyboard: InlineKeyboard | None = None


@dataclass(frozen=True, slots=True)
class CallbackAnswer:
    callback_query_id: str
    text: str | None = None


type Outgoing = Reply | EditMessage | CallbackAnswer
