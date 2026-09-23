"""Run the bot locally with long polling (docs/telegram_client_design.md, T2).

For development only: a webhook needs a public HTTPS address that a developer
machine usually lacks. Every update goes through the same handler composition
as the webhook, deduplication included. Refuses to run outside development and
test, and removes any registered webhook first (Telegram does not serve
getUpdates while one is set).
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from typing import Final

from mintflow.config import get_settings
from mintflow.http.telegram import build_telegram_update_handler
from mintflow.infrastructure.persistence import create_database_engine, create_session_factory
from mintflow.logging import configure_logging
from mintflow.telegram import TelegramApiError, TelegramBotApi, TelegramUpdate
from mintflow.telegram.runtime import build_telegram_runtime

LONG_POLL_SECONDS: Final = 30
RETRY_DELAY_SECONDS: Final = 2.0
_ALLOWED_ENVIRONMENTS: Final = frozenset({"development", "test"})

logger = logging.getLogger("mintflow.commands.telegram_polling")


def poll(
    bot_api: TelegramBotApi,
    handle: Callable[[TelegramUpdate], None],
    *,
    should_continue: Callable[[], bool] = lambda: True,
    timeout_seconds: int = LONG_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Fetch updates after the last seen id and hand each one to ``handle``.

    The offset always advances past an update, even one whose handling failed:
    locally, retrying a failing update forever helps nobody, and the failure is
    logged by exception type only, since exception details can carry user input.
    (The webhook, by contrast, lets Telegram redeliver.)
    """
    offset: int | None = None
    while should_continue():
        try:
            updates = bot_api.get_updates(offset=offset, timeout_seconds=timeout_seconds)
        except TelegramApiError as error:
            logger.warning("telegram_poll_failed method=%s code=%s", error.method, error.error_code)
            sleep(RETRY_DELAY_SECONDS)
            continue
        for update in updates:
            try:
                handle(update)
            except Exception as error:
                logger.error(
                    "telegram_update_failed update_id=%s error=%s",
                    update.update_id,
                    type(error).__name__,
                )
            offset = update.update_id + 1


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Run the MintFlow bot locally with long polling."
    ).parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.environment not in _ALLOWED_ENVIRONMENTS:
        print("telegram polling runs only in development or test", file=sys.stderr)
        return 2
    runtime = build_telegram_runtime(settings)
    if runtime is None:
        print("telegram is not configured", file=sys.stderr)
        return 2

    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)

    def handle(update: TelegramUpdate) -> None:
        with session_factory() as session:
            build_telegram_update_handler(session, runtime).handle(update)

    try:
        runtime.bot_api.delete_webhook()
        poll(runtime.bot_api, handle)
    except KeyboardInterrupt:
        return 0
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
