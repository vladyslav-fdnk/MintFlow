"""The deployed SMTP sender (docs/operations_design.md, O3)."""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from types import TracebackType
from typing import Self

import pytest

from mintflow.application.authentication.login_challenge import (
    EmailDeliveryError,
    MagicLinkMessage,
)
from mintflow.infrastructure.email import MAGIC_LINK_SUBJECT, SmtpEmailSender, SmtpTls

RECIPIENT = "Person@example.com"
MAGIC_LINK = "https://app.mintflow.test/auth/magic-link?token=raw-secret-token"
PASSWORD = "re_api_key_secret"
CONTEXT = ssl.create_default_context()


class RecordingSmtp:
    """An SMTP connection that records the calls a sender makes, in order."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.messages: list[EmailMessage] = []
        self.fail_on = fail_on

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.calls.append(("quit", None))

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            errors: dict[str, Exception] = {
                "starttls": ssl.SSLCertVerificationError("certificate verify failed"),
                "login": smtplib.SMTPAuthenticationError(
                    535, b"bad credentials " + PASSWORD.encode()
                ),
                "send": smtplib.SMTPRecipientsRefused({RECIPIENT: (550, b"no such user")}),
            }
            raise errors[name]

    def starttls(self, *, context: ssl.SSLContext) -> object:
        self.calls.append(("starttls", context))
        self._maybe_fail("starttls")
        return (220, b"ready")

    def login(self, user: str, password: str) -> object:
        self.calls.append(("login", (user, password)))
        self._maybe_fail("login")
        return (235, b"ok")

    def send_message(self, message: EmailMessage) -> object:
        self.calls.append(("send", None))
        self._maybe_fail("send")
        self.messages.append(message)
        return {}


def _sender(
    tls: SmtpTls, connection: RecordingSmtp, opened: list[tuple[object, ...]]
) -> SmtpEmailSender:
    def factory(
        host: str, port: int, timeout: float, mode: SmtpTls, context: ssl.SSLContext
    ) -> RecordingSmtp:
        opened.append((host, port, timeout, mode, context))
        return connection

    return SmtpEmailSender(
        host="smtp.resend.com",
        port=465 if tls == "implicit" else 587,
        tls=tls,
        username="resend",
        password=PASSWORD,
        from_email="no-reply@mintflow.example",
        timeout_seconds=10,
        smtp_factory=factory,
        tls_context=lambda: CONTEXT,
    )


def test_implicit_tls_logs_in_and_sends_the_link() -> None:
    connection, opened = RecordingSmtp(), list[tuple[object, ...]]()

    _sender("implicit", connection, opened).send_magic_link(MagicLinkMessage(RECIPIENT, MAGIC_LINK))

    assert opened == [("smtp.resend.com", 465, 10, "implicit", CONTEXT)]
    assert [name for name, _ in connection.calls] == ["login", "send", "quit"]
    assert connection.calls[0] == ("login", ("resend", PASSWORD))
    message = connection.messages[0]
    assert (message["From"], message["To"], message["Subject"]) == (
        "no-reply@mintflow.example",
        RECIPIENT,
        MAGIC_LINK_SUBJECT,
    )
    assert MAGIC_LINK in message.get_content()


def test_starttls_encrypts_before_the_login() -> None:
    connection, opened = RecordingSmtp(), list[tuple[object, ...]]()

    _sender("starttls", connection, opened).send_magic_link(MagicLinkMessage(RECIPIENT, MAGIC_LINK))

    assert opened[0][1:4] == (587, 10, "starttls")
    assert [name for name, _ in connection.calls] == ["starttls", "login", "send", "quit"]
    assert connection.calls[0] == ("starttls", CONTEXT)


def test_the_default_tls_context_verifies_the_server() -> None:
    sender = SmtpEmailSender(
        host="smtp.resend.com",
        port=465,
        tls="implicit",
        username="resend",
        password=PASSWORD,
        from_email="no-reply@mintflow.example",
        timeout_seconds=10,
    )

    context = sender._tls_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize(
    ("tls", "fail_on"), [("starttls", "starttls"), ("implicit", "login"), ("implicit", "send")]
)
def test_every_failure_is_one_generic_error_without_secrets_in_logs(
    tls: SmtpTls, fail_on: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    connection = RecordingSmtp(fail_on=fail_on)

    with pytest.raises(EmailDeliveryError) as raised:
        _sender(tls, connection, []).send_magic_link(MagicLinkMessage(RECIPIENT, MAGIC_LINK))

    assert str(raised.value) == "email delivery failed"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    for secret in (MAGIC_LINK, "raw-secret-token", RECIPIENT, PASSWORD):
        assert secret not in caplog.text
    assert ("quit", None) in connection.calls


def test_a_connection_that_cannot_open_is_the_same_generic_error() -> None:
    def refused(*_args: object) -> RecordingSmtp:
        raise ConnectionRefusedError("connection refused")

    sender = SmtpEmailSender(
        host="smtp.resend.com",
        port=465,
        tls="implicit",
        username="resend",
        password=PASSWORD,
        from_email="no-reply@mintflow.example",
        timeout_seconds=10,
        smtp_factory=refused,
        tls_context=lambda: CONTEXT,
    )

    with pytest.raises(EmailDeliveryError):
        sender.send_magic_link(MagicLinkMessage(RECIPIENT, MAGIC_LINK))
