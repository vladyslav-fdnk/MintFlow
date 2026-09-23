import logging

PROJECT_LOGGER_NAME = "mintflow"
UVICORN_ACCESS_LOGGER_NAME = "uvicorn.access"
# httpx logs every request URL at INFO. Telegram Bot API URLs contain the bot token,
# so these clients never log below WARNING, whatever the application level is.
URL_LOGGING_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


def configure_logging(level: str) -> None:
    """Configure concise process-wide development logging."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logging.getLogger(PROJECT_LOGGER_NAME).setLevel(level)
    logging.getLogger(UVICORN_ACCESS_LOGGER_NAME).disabled = True
    for name in URL_LOGGING_HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
