import logging

PROJECT_LOGGER_NAME = "mintflow"


def configure_logging(level: str) -> None:
    """Configure concise process-wide development logging."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logging.getLogger(PROJECT_LOGGER_NAME).setLevel(level)
