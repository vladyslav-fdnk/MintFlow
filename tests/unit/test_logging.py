import logging
from collections.abc import Iterator

import pytest

from mintflow.logging import URL_LOGGING_HTTP_CLIENT_LOGGERS, configure_logging


@pytest.fixture(autouse=True)
def _restore_levels() -> Iterator[None]:
    names = ["", "mintflow", *URL_LOGGING_HTTP_CLIENT_LOGGERS]
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.parametrize("level", ["DEBUG", "INFO"])
def test_http_client_loggers_never_log_request_urls(level: str) -> None:
    # httpx logs every request URL at INFO; Telegram Bot API URLs contain the bot token.
    configure_logging(level)

    for name in URL_LOGGING_HTTP_CLIENT_LOGGERS:
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
    assert logging.getLogger("mintflow").isEnabledFor(getattr(logging, level))
