"""The webhook registration deploy step (docs/operations_design.md, O6)."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from mintflow.commands import register_telegram_webhook
from mintflow.config import get_settings
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi

TOKEN = "123456:secret-bot-token"
SECRET = "webhook-secret-value"


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch: pytest.MonkeyPatch, *, origin: str, telegram: bool = True) -> None:
    values = {
        "MINTFLOW_ENVIRONMENT": "test",
        "MINTFLOW_LOG_LEVEL": "CRITICAL",
        "MINTFLOW_DATABASE_URL": "postgresql://localhost/mintflow_unused",
        "MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY": "rate",
        "MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY": "csrf",
        "MINTFLOW_AUTHENTICATION_WEB_ORIGIN": origin,
        "MINTFLOW_AUTHENTICATION_RETURN_TARGETS": '["dashboard"]',
    }
    if telegram:
        values |= {
            "MINTFLOW_TELEGRAM_BOT_TOKEN": TOKEN,
            "MINTFLOW_TELEGRAM_BOT_USERNAME": "mintflow_bot",
            "MINTFLOW_TELEGRAM_WEBHOOK_SECRET": SECRET,
        }
    else:
        for name in ("TOKEN", "USERNAME", "WEBHOOK_SECRET"):
            monkeypatch.delenv(f"MINTFLOW_TELEGRAM_BOT_{name}", raising=False)
            monkeypatch.delenv(f"MINTFLOW_TELEGRAM_{name}", raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _recording_runtime(monkeypatch: pytest.MonkeyPatch, bot: RecordingTelegramBotApi) -> None:
    real_build = register_telegram_webhook.build_telegram_runtime

    def build(settings: object) -> TelegramRuntime | None:
        runtime = real_build(settings)  # type: ignore[arg-type]
        if runtime is None:
            return None
        return TelegramRuntime(
            bot_api=bot,
            bot_username=runtime.bot_username,
            webhook_secret=runtime.webhook_secret,
            web_origin=runtime.web_origin,
            clock=runtime.clock,
        )

    monkeypatch.setattr(register_telegram_webhook, "build_telegram_runtime", build)


def test_registers_the_webhook_url_with_the_secret() -> None:
    bot = RecordingTelegramBotApi()

    url = register_telegram_webhook.register_webhook(
        bot, web_origin="https://app.mintflow.example/", secret=SecretStr(SECRET)
    )

    assert url == "https://app.mintflow.example/telegram/webhook"
    [call] = bot.calls_to("set_webhook")
    assert call.arguments["url"] == url


def test_the_command_registers_and_prints_only_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch, origin="https://app.mintflow.example")
    bot = RecordingTelegramBotApi()
    _recording_runtime(monkeypatch, bot)

    assert register_telegram_webhook.main([]) == 0

    output = capsys.readouterr()
    assert "https://app.mintflow.example/telegram/webhook" in output.out
    assert TOKEN not in output.out + output.err and SECRET not in output.out + output.err
    assert len(bot.calls_to("set_webhook")) == 1


def test_running_it_twice_is_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, origin="https://app.mintflow.example")
    bot = RecordingTelegramBotApi()
    _recording_runtime(monkeypatch, bot)

    assert register_telegram_webhook.main([]) == 0
    assert register_telegram_webhook.main([]) == 0
    first, second = bot.calls_to("set_webhook")
    assert first.arguments == second.arguments


def test_without_telegram_the_command_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch, origin="https://app.mintflow.example", telegram=False)

    assert register_telegram_webhook.main([]) == 2
    assert "telegram is not configured" in capsys.readouterr().err


def test_an_http_origin_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch, origin="http://localhost:8000")
    bot = RecordingTelegramBotApi()
    _recording_runtime(monkeypatch, bot)

    assert register_telegram_webhook.main([]) == 2
    assert "https://" in capsys.readouterr().err
    assert bot.calls_to("set_webhook") == []


def test_a_bot_api_failure_is_reported_without_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch, origin="https://app.mintflow.example")
    bot = RecordingTelegramBotApi(fail_methods={"set_webhook"})
    _recording_runtime(monkeypatch, bot)

    assert register_telegram_webhook.main([]) == 1

    output = capsys.readouterr()
    assert "setWebhook failed" in output.err
    assert TOKEN not in output.out + output.err and SECRET not in output.out + output.err
