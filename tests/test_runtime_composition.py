from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from mintflow.application.authentication.login_challenge import EmailSender, MagicLinkMessage
from mintflow.config import Settings
from mintflow.http.authentication import AuthenticationConfigurationError
from mintflow.main import create_app


class RecordingEmailSender:
    def __init__(self) -> None:
        self.messages: list[MagicLinkMessage] = []

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        self.messages.append(message)


def test_runtime_constructs_and_uses_explicitly_configured_mailpit_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_settings = Settings(
        environment="test",
        database_url=SecretStr("postgresql://test:test@localhost:5432/test"),
        authentication_rate_limit_key=SecretStr("test-rate-limit-key"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
        email_backend="mailpit",
        mailpit_smtp_host="mailpit",
        mailpit_from_email="no-reply@mintflow.dev",
    )
    sender = RecordingEmailSender()

    def create_sender(settings: Settings) -> EmailSender:
        assert settings is configured_settings
        return sender

    monkeypatch.setattr("mintflow.main.create_local_email_sender", create_sender)

    application = create_app(configured_settings)
    message = MagicLinkMessage(
        recipient_email="person@example.com",
        magic_link="https://app.mintflow.test/auth/magic-link?token=secret",
    )
    application.state.authentication_email_sender.send_magic_link(message)

    assert sender.messages == [message]


def test_runtime_keeps_health_shell_available_without_email_adapter(settings: Settings) -> None:
    configured_settings = settings.model_copy(
        update={
            "email_backend": None,
            "mailpit_smtp_host": None,
            "mailpit_from_email": None,
        }
    )

    application = create_app(configured_settings)

    assert application.state.authentication_email_sender is None


@pytest.mark.anyio
async def test_runtime_disposes_database_engine_on_lifespan_exit(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    application = create_app(settings)
    dispose = Mock(wraps=application.state.database_engine.dispose)
    monkeypatch.setattr(application.state.database_engine, "dispose", dispose)

    async with application.router.lifespan_context(application):
        dispose.assert_not_called()

    dispose.assert_called_once_with()


def test_runtime_disposes_database_engine_when_composition_fails(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    database_engine = Mock()
    monkeypatch.setattr("mintflow.main.create_database_engine", Mock(return_value=database_engine))
    monkeypatch.setattr(
        "mintflow.main.build_authentication_runtime",
        Mock(side_effect=ValueError("invalid runtime")),
    )

    with pytest.raises(AuthenticationConfigurationError):
        create_app(settings)

    database_engine.dispose.assert_called_once_with()
