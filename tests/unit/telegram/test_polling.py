import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from mintflow.commands import telegram_polling
from mintflow.config import Settings, get_settings
from mintflow.telegram import TelegramUpdate
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi


def _update(update_id: int, text: str = "hello") -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": 1,
                "date": 0,
                "text": text,
                "from": {"id": 5, "is_bot": False, "first_name": "A"},
                "chat": {"id": 5, "type": "private"},
            },
        }
    )


@dataclass
class Rounds:
    """Lets the loop run a fixed number of rounds."""

    remaining: int
    calls: int = field(default=0)

    def __call__(self) -> bool:
        self.calls += 1
        self.remaining -= 1
        return self.remaining >= 0


def test_updates_are_handled_in_order_and_the_offset_advances() -> None:
    bot = RecordingTelegramBotApi(pending_updates=[_update(5), _update(6), _update(8)])
    handled: list[int] = []

    telegram_polling.poll(
        bot, lambda update: handled.append(update.update_id), should_continue=Rounds(2)
    )

    assert handled == [5, 6, 8]
    offsets = [call.arguments["offset"] for call in bot.calls_to("get_updates")]
    assert offsets == [None, 9]


def test_a_failing_update_is_logged_by_type_only_and_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = RecordingTelegramBotApi(pending_updates=[_update(1, "secret text"), _update(2)])
    handled: list[int] = []

    def handle(update: TelegramUpdate) -> None:
        if update.update_id == 1:
            raise RuntimeError("secret text leaked into an error")
        handled.append(update.update_id)

    poll_logger = logging.getLogger("mintflow.commands.telegram_polling")
    poll_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.ERROR, logger=poll_logger.name):
            telegram_polling.poll(bot, handle, should_continue=Rounds(2))
    finally:
        poll_logger.removeHandler(caplog.handler)

    assert handled == [2]
    assert "telegram_update_failed update_id=1 error=RuntimeError" in caplog.text
    assert "secret text" not in caplog.text
    assert bot.calls_to("get_updates")[-1].arguments["offset"] == 3


def test_api_errors_back_off_and_keep_polling() -> None:
    bot = RecordingTelegramBotApi(fail_methods={"get_updates"})
    sleeps: list[float] = []

    telegram_polling.poll(bot, lambda update: None, should_continue=Rounds(3), sleep=sleeps.append)

    assert len(bot.calls_to("get_updates")) == 3
    assert sleeps == [telegram_polling.RETRY_DELAY_SECONDS] * 3


def _configure(monkeypatch: pytest.MonkeyPatch, *, environment: str) -> None:
    for name, value in {
        "MINTFLOW_ENVIRONMENT": environment,
        "MINTFLOW_LOG_LEVEL": "CRITICAL",
        "MINTFLOW_DATABASE_URL": "postgresql://localhost/mintflow_unused",
        "MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY": "rate",
        "MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY": "csrf",
        "MINTFLOW_AUTHENTICATION_WEB_ORIGIN": "https://app.mintflow.test",
        "MINTFLOW_AUTHENTICATION_RETURN_TARGETS": '["dashboard"]',
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_refuses_to_run_outside_development(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], environment: str
) -> None:
    _configure(monkeypatch, environment=environment)

    assert telegram_polling.main([]) == 2
    assert "only in development or test" in capsys.readouterr().err


def test_refuses_to_run_without_telegram_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch, environment="development")

    assert telegram_polling.main([]) == 2
    assert "not configured" in capsys.readouterr().err


class InterruptingBot(RecordingTelegramBotApi):
    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[TelegramUpdate]:
        self._record("get_updates", offset=offset, timeout_seconds=timeout_seconds)
        raise KeyboardInterrupt


def test_removes_the_webhook_and_stops_cleanly_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, environment="development")
    bot = InterruptingBot()

    def runtime(settings: Settings) -> TelegramRuntime:
        return TelegramRuntime(
            bot_api=bot,
            bot_username="mintflow_test_bot",
            webhook_secret=SecretStr("hook-secret"),
            web_origin=settings.authentication_web_origin,
            clock=lambda: datetime.now(UTC),
        )

    monkeypatch.setattr(telegram_polling, "build_telegram_runtime", runtime)

    assert telegram_polling.main([]) == 0
    assert [call.method for call in bot.calls] == ["delete_webhook", "get_updates"]
