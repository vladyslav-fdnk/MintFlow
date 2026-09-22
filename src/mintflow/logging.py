import logging

PROJECT_LOGGER_NAME = "mintflow"
UVICORN_ACCESS_LOGGER_NAME = "uvicorn.access"


def configure_logging(level: str) -> None:
    """Configure concise process-wide development logging."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logging.getLogger(PROJECT_LOGGER_NAME).setLevel(level)
    logging.getLogger(UVICORN_ACCESS_LOGGER_NAME).disabled = True
