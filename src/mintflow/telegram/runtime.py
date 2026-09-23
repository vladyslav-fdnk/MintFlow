from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import SecretStr

from mintflow.config import Settings
from mintflow.telegram.bot_api import HttpxTelegramBotApi, TelegramBotApi


@dataclass(frozen=True, slots=True)
class TelegramRuntime:
    """Process-wide Telegram composition; absent when Telegram is not configured."""

    bot_api: TelegramBotApi
    bot_username: str
    webhook_secret: SecretStr
    web_origin: str
    clock: Callable[[], datetime]


def build_telegram_runtime(
    settings: Settings, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> TelegramRuntime | None:
    if (
        settings.telegram_bot_token is None
        or settings.telegram_bot_username is None
        or settings.telegram_webhook_secret is None
    ):
        return None
    return TelegramRuntime(
        bot_api=HttpxTelegramBotApi(token=settings.telegram_bot_token),
        bot_username=settings.telegram_bot_username,
        webhook_secret=settings.telegram_webhook_secret,
        web_origin=settings.authentication_web_origin,
        clock=clock,
    )
