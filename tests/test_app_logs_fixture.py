import logging

import pytest

from mintflow.config import Settings
from mintflow.main import create_app


def test_app_logs_sees_records_logged_after_create_app(
    settings: Settings, app_logs: pytest.LogCaptureFixture
) -> None:
    create_app(settings)

    logging.getLogger("mintflow.probe").debug("project debug record")
    logging.getLogger("thirdparty.probe").warning("library warning record")

    assert "project debug record" in app_logs.text
    assert "library warning record" in app_logs.text


def test_plain_caplog_misses_them_which_is_why_app_logs_exists(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    create_app(settings)

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("mintflow.probe").warning("lost record")

    assert "lost record" not in caplog.text


def test_telegram_bot_token_never_reaches_the_logs(
    settings: Settings, app_logs: pytest.LogCaptureFixture
) -> None:
    """httpx logs request URLs at INFO; Bot API URLs contain the token."""
    import httpx
    from pydantic import SecretStr

    from mintflow.telegram import HttpxTelegramBotApi

    token = "123456:token-that-must-not-be-logged"
    create_app(settings.model_copy(update={"log_level": "DEBUG"}))
    api = HttpxTelegramBotApi(
        token=SecretStr(token),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
            )
        ),
    )

    api.send_message(chat_id=1, text="hello")

    assert token not in app_logs.text
