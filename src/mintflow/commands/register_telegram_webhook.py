"""Point Telegram at this deployment: ``python -m mintflow.commands.register_telegram_webhook``.

A deploy step (docs/operations_design.md, O6): it calls ``setWebhook`` with
``<web origin>/telegram/webhook`` and the configured secret token, which Telegram then sends with
every update. Calling it again with the same values changes nothing, so every deploy runs it.
Neither the bot token nor the secret is ever printed or logged.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import Final

from pydantic import SecretStr

from mintflow.config import get_settings
from mintflow.logging import configure_logging
from mintflow.telegram import TelegramApiError, TelegramBotApi
from mintflow.telegram.runtime import build_telegram_runtime

WEBHOOK_PATH: Final = "/telegram/webhook"


class WebhookOriginError(ValueError):
    """Telegram delivers webhooks only to an https address."""


def webhook_url(web_origin: str) -> str:
    if not web_origin.startswith("https://"):
        raise WebhookOriginError("the Web origin must be an https:// URL for a Telegram webhook")
    return web_origin.rstrip("/") + WEBHOOK_PATH


def register_webhook(bot_api: TelegramBotApi, *, web_origin: str, secret: SecretStr) -> str:
    """Register the webhook and return its URL. Raises WebhookOriginError or TelegramApiError."""
    url = webhook_url(web_origin)
    bot_api.set_webhook(url=url, secret_token=secret)
    return url


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="Register this deployment's Telegram webhook.").parse_args(
        argv
    )
    settings = get_settings()
    configure_logging(settings.log_level)
    runtime = build_telegram_runtime(settings)
    if runtime is None:
        print("telegram is not configured", file=sys.stderr)
        return 2
    try:
        url = register_webhook(
            runtime.bot_api, web_origin=runtime.web_origin, secret=runtime.webhook_secret
        )
    except WebhookOriginError as error:
        print(str(error), file=sys.stderr)
        return 2
    except TelegramApiError as error:
        print(f"setWebhook failed (error code {error.error_code})", file=sys.stderr)
        return 1
    print(f"telegram webhook registered at {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
