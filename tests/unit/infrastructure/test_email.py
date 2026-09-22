import logging
import smtplib
from email.message import EmailMessage
from types import TracebackType
from typing import Self

import pytest

from mintflow.application.authentication.login_challenge import (
    EmailDeliveryError,
    MagicLinkMessage,
)
from mintflow.infrastructure.email import MAGIC_LINK_SUBJECT, MailpitEmailSender

RECIPIENT = "Person@example.com"
MAGIC_LINK = "https://app.mintflow.test/auth/magic-link?token=raw-secret-token"
RAW_TOKEN = "raw-secret-token"


class RecordingSmtpConnection:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def send_message(self, message: EmailMessage) -> object:
        self.messages.append(message)
        return {}


class FailingSmtpConnection(RecordingSmtpConnection):
    def send_message(self, message: EmailMessage) -> object:
        raise smtplib.SMTPServerDisconnected(f"failure while sending {message}")


def test_maps_typed_magic_link_message_to_smtp_message() -> None:
    connection = RecordingSmtpConnection()
    sender = MailpitEmailSender(
        host="mailpit",
        port=1025,
        from_email="no-reply@mintflow.dev",
        timeout_seconds=3,
        smtp_factory=lambda host, port, timeout: connection,
    )

    sender.send_magic_link(MagicLinkMessage(RECIPIENT, MAGIC_LINK))

    assert len(connection.messages) == 1
    message = connection.messages[0]
    assert message["From"] == "no-reply@mintflow.dev"
    assert message["To"] == RECIPIENT
    assert message["Subject"] == MAGIC_LINK_SUBJECT
    assert MAGIC_LINK in message.get_content()


def test_expected_smtp_failure_is_safe_and_does_not_log_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = MailpitEmailSender(
        host="mailpit",
        port=1025,
        from_email="no-reply@mintflow.dev",
        timeout_seconds=3,
        smtp_factory=lambda host, port, timeout: FailingSmtpConnection(),
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(EmailDeliveryError) as captured:
        sender.send_magic_link(MagicLinkMessage(RECIPIENT, MAGIC_LINK))

    assert str(captured.value) == "email delivery failed"
    assert captured.value.__cause__ is None
    assert MAGIC_LINK not in str(captured.value)
    assert RAW_TOKEN not in str(captured.value)
    assert all(MAGIC_LINK not in record.getMessage() for record in caplog.records)
    assert all(RAW_TOKEN not in record.getMessage() for record in caplog.records)
