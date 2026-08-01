import pytest
from pydantic import SecretStr

from mintflow.application.authentication.login_challenge import EmailSender, MagicLinkMessage
from mintflow.config import Settings
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


def test_runtime_has_no_email_sender_when_backend_is_not_selected(settings: Settings) -> None:
    application = create_app(settings)

    assert application.state.authentication_email_sender is None
