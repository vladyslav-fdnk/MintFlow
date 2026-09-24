import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from types import TracebackType
from typing import Literal, Protocol, Self

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
        return magic_link_email(from_email=self._from_email, message=message)


def magic_link_email(*, from_email: str, message: MagicLinkMessage) -> EmailMessage:
    smtp_message = EmailMessage()
    smtp_message["From"] = from_email
    smtp_message["To"] = message.recipient_email
    smtp_message["Subject"] = MAGIC_LINK_SUBJECT
    smtp_message.set_content(
        "Use this link to sign in to MintFlow:\n\n"
        f"{message.magic_link}\n\n"
        "If you did not request this link, you can ignore this email."
    )
    return smtp_message


SmtpTls = Literal["implicit", "starttls"]


class AuthenticatedSmtpConnection(SmtpConnection, Protocol):
    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...


SecureSmtpFactory = Callable[
    [str, int, float, SmtpTls, ssl.SSLContext], AuthenticatedSmtpConnection
]


def _open_secure_smtp(
    host: str, port: int, timeout_seconds: float, tls: SmtpTls, context: ssl.SSLContext
) -> AuthenticatedSmtpConnection:
    if tls == "implicit":
        return smtplib.SMTP_SSL(host=host, port=port, timeout=timeout_seconds, context=context)
    return smtplib.SMTP(host=host, port=port, timeout=timeout_seconds)


class SmtpEmailSender:
    """Deliver sign-in email through a deployed SMTP provider such as Resend (operations O3).

    Every connection is encrypted and the server's certificate and name are verified: implicit
    TLS from the first byte, or STARTTLS before the login. Any failure becomes the same generic
    delivery error, so neither the link nor the recipient reaches a log.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        tls: SmtpTls,
        username: str,
        password: str,
        from_email: str,
        timeout_seconds: float,
        smtp_factory: SecureSmtpFactory = _open_secure_smtp,
        tls_context: Callable[[], ssl.SSLContext] = ssl.create_default_context,
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._username = username
        self._password = password
        self._from_email = from_email
        self._timeout_seconds = timeout_seconds
        self._smtp_factory = smtp_factory
        self._tls_context = tls_context

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        smtp_message = magic_link_email(from_email=self._from_email, message=message)
        try:
            context = self._tls_context()
            with self._smtp_factory(
                self._host, self._port, self._timeout_seconds, self._tls, context
            ) as connection:
                if self._tls == "starttls":
                    connection.starttls(context=context)
                connection.login(self._username, self._password)
                connection.send_message(smtp_message)
        except (OSError, smtplib.SMTPException):
            raise EmailDeliveryError("email delivery failed") from None


def create_email_sender(settings: Settings) -> EmailSender | None:
    """The configured sender: Mailpit locally, an SMTP provider when deployed, else none."""
    if settings.email_backend == "mailpit":
        return create_local_email_sender(settings)
    if settings.email_backend != "smtp":
        return None
    if (
        settings.smtp_host is None
        or settings.smtp_username is None
        or settings.smtp_password is None
        or settings.smtp_from_email is None
    ):
        raise ValueError("SMTP configuration is incomplete")
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        tls=settings.smtp_tls,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value(),
        from_email=str(settings.smtp_from_email),
        timeout_seconds=settings.smtp_timeout_seconds,
    )


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
