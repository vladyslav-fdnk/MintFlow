import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from types import TracebackType
from typing import Protocol, Self

from mintflow.application.authentication.login_challenge import (
    EmailDeliveryError,
    EmailSender,
    MagicLinkMessage,
)
from mintflow.config import Settings

MAGIC_LINK_SUBJECT = "Your MintFlow sign-in link"


class SmtpConnection(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def send_message(self, message: EmailMessage) -> object: ...


SmtpFactory = Callable[[str, int, float], SmtpConnection]


def _open_smtp(host: str, port: int, timeout_seconds: float) -> SmtpConnection:
    return smtplib.SMTP(host=host, port=port, timeout=timeout_seconds)


class MailpitEmailSender:
    """Deliver typed authentication messages to a local Mailpit SMTP service."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_email: str,
        timeout_seconds: float,
        smtp_factory: SmtpFactory = _open_smtp,
    ) -> None:
        self._host = host
        self._port = port
        self._from_email = from_email
        self._timeout_seconds = timeout_seconds
        self._smtp_factory = smtp_factory

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        smtp_message = self._to_smtp_message(message)
        try:
            with self._smtp_factory(self._host, self._port, self._timeout_seconds) as connection:
                connection.send_message(smtp_message)
        except (OSError, smtplib.SMTPException):
            raise EmailDeliveryError("email delivery failed") from None

    def _to_smtp_message(self, message: MagicLinkMessage) -> EmailMessage:
        smtp_message = EmailMessage()
        smtp_message["From"] = self._from_email
        smtp_message["To"] = message.recipient_email
        smtp_message["Subject"] = MAGIC_LINK_SUBJECT
        smtp_message.set_content(
            "Use this link to sign in to MintFlow:\n\n"
            f"{message.magic_link}\n\n"
            "If you did not request this link, you can ignore this email."
        )
        return smtp_message


def create_local_email_sender(settings: Settings) -> EmailSender:
    """Build the configured local sender without exposing Mailpit to application code."""

    if settings.email_backend != "mailpit":
        raise ValueError("the local Mailpit email backend is not configured")
    if settings.environment not in {"development", "test"}:
        raise ValueError("Mailpit email delivery is allowed only in development or test")
    if settings.mailpit_smtp_host is None or settings.mailpit_from_email is None:
        raise ValueError("Mailpit SMTP configuration is incomplete")
    return MailpitEmailSender(
        host=settings.mailpit_smtp_host,
        port=settings.mailpit_smtp_port,
        from_email=str(settings.mailpit_from_email),
        timeout_seconds=settings.mailpit_smtp_timeout_seconds,
    )
