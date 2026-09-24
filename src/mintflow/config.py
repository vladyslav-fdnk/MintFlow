import re
from functools import lru_cache
from ipaddress import ip_network
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Telegram's own rules for bot usernames and webhook secret tokens.
_TELEGRAM_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")
_TELEGRAM_WEBHOOK_SECRET = re.compile(r"[A-Za-z0-9_-]{1,256}")


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    enable_api_docs: bool = False
    database_url: SecretStr
    authentication_rate_limit_key: SecretStr
    authentication_csrf_signing_key: SecretStr
    authentication_web_origin: str = Field(min_length=1)
    authentication_return_targets: frozenset[str]
    email_backend: Literal["mailpit", "smtp"] | None = None
    mailpit_smtp_host: str | None = Field(default=None, min_length=1)
    mailpit_smtp_port: int = Field(default=1025, ge=1, le=65535)
    mailpit_smtp_timeout_seconds: float = Field(default=5.0, gt=0)
    mailpit_from_email: EmailStr | None = None
    # A deployed SMTP provider such as Resend (docs/operations_design.md, O3). Only encrypted
    # transports exist: implicit TLS (port 465) or STARTTLS (port 587), always with a login.
    smtp_host: str | None = Field(default=None, min_length=1)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_tls: Literal["implicit", "starttls"] = "implicit"
    smtp_username: str | None = Field(default=None, min_length=1)
    smtp_password: SecretStr | None = None
    smtp_from_email: EmailStr | None = None
    smtp_timeout_seconds: float = Field(default=10.0, gt=0)
    # Reverse proxies whose X-Forwarded-For and X-Forwarded-Proto are believed (addresses or
    # networks, O2). Empty: forwarding headers are ignored and the direct peer is the client.
    trusted_proxies: frozenset[str] = frozenset()
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    receipt_recognizer: Literal["fake", "azure"] | None = None
    azure_document_intelligence_endpoint: str | None = None
    azure_document_intelligence_key: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MINTFLOW_",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_local_email_backend(self) -> "Settings":
        """Keep the Mailpit transport explicit and isolated from deployed environments."""

        if self.email_backend != "mailpit":
            return self
        if self.environment not in {"development", "test"}:
            raise ValueError("Mailpit email delivery is allowed only in development or test")
        if self.mailpit_smtp_host is None or self.mailpit_from_email is None:
            raise ValueError("Mailpit requires an SMTP host and sender address")
        return self

    @model_validator(mode="after")
    def validate_smtp_email_backend(self) -> "Settings":
        """A deployed SMTP provider needs every connection value; none of them is echoed."""

        if self.email_backend != "smtp":
            return self
        if (
            self.smtp_host is None
            or self.smtp_username is None
            or self.smtp_password is None
            or self.smtp_from_email is None
        ):
            raise ValueError("SMTP email requires a host, username, password, and sender address")
        return self

    @field_validator("trusted_proxies")
    @classmethod
    def validate_trusted_proxies(cls, value: frozenset[str]) -> frozenset[str]:
        for entry in value:
            try:
                ip_network(entry, strict=False)
            except ValueError:
                raise ValueError("trusted proxies must be IP addresses or networks") from None
        return value

    @model_validator(mode="after")
    def validate_telegram_configuration(self) -> "Settings":
        """Telegram is enabled only by all three values together; none of them is echoed."""

        provided = [
            self.telegram_bot_token is not None,
            self.telegram_bot_username is not None,
            self.telegram_webhook_secret is not None,
        ]
        if any(provided) and not all(provided):
            raise ValueError(
                "Telegram requires a bot token, bot username, and webhook secret together"
            )
        if self.telegram_bot_username is not None and not _TELEGRAM_USERNAME.fullmatch(
            self.telegram_bot_username
        ):
            raise ValueError("Telegram bot username must be 5-32 letters, digits, or underscores")
        if self.telegram_webhook_secret is not None and not _TELEGRAM_WEBHOOK_SECRET.fullmatch(
            self.telegram_webhook_secret.get_secret_value()
        ):
            raise ValueError(
                "Telegram webhook secret must be 1-256 letters, digits, underscores, or hyphens"
            )
        return self

    @model_validator(mode="after")
    def validate_receipt_recognizer(self) -> "Settings":
        """The fake recognizer reads nothing; it exists for development and tests only."""

        if self.receipt_recognizer == "fake" and self.environment not in {"development", "test"}:
            raise ValueError("the fake receipt recognizer is allowed only in development or test")
        if self.receipt_recognizer == "azure":
            endpoint = self.azure_document_intelligence_endpoint
            if endpoint is None or self.azure_document_intelligence_key is None:
                raise ValueError(
                    "the azure receipt recognizer requires a Document Intelligence endpoint and key"
                )
            if not endpoint.startswith("https://"):
                raise ValueError("the Document Intelligence endpoint must be an https URL")
        return self

    @property
    def telegram_enabled(self) -> bool:
        return self.telegram_bot_token is not None

    @field_validator("authentication_return_targets")
    @classmethod
    def validate_authentication_return_targets(cls, value: frozenset[str]) -> frozenset[str]:
        if not value:
            raise ValueError("at least one authentication return target is required")
        if any(not target or len(target) > 64 for target in value):
            raise ValueError("authentication return targets must contain 1 to 64 characters")
        return value


class ProductionConfigurationError(RuntimeError):
    """The Web application refuses to start with a configuration unfit for production."""


def production_configuration_problems(settings: Settings) -> list[str]:
    """What stops ``settings`` from serving production (docs/operations_design.md, O4).

    Only the Web application checks this: commands such as the receipt worker or the cleanups
    run with the same environment file but need neither email nor Telegram themselves.
    """
    if settings.environment != "production":
        return []
    problems = []
    if settings.enable_api_docs:
        problems.append("API docs must be off")
    if not settings.authentication_web_origin.startswith("https://"):
        problems.append("the Web origin must be an https:// URL")
    if settings.email_backend != "smtp":
        problems.append("the email backend must be smtp")
    if not settings.telegram_enabled:
        problems.append("the Telegram bot token, username, and webhook secret are required")
    if settings.receipt_recognizer == "fake":
        problems.append("the fake receipt recognizer is not allowed")
    return problems


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-level configuration."""

    return Settings()  # type: ignore[call-arg]
