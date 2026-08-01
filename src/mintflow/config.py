from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    enable_api_docs: bool = False
    database_url: SecretStr
    authentication_rate_limit_key: SecretStr
    email_backend: Literal["mailpit"] | None = None
    mailpit_smtp_host: str | None = Field(default=None, min_length=1)
    mailpit_smtp_port: int = Field(default=1025, ge=1, le=65535)
    mailpit_smtp_timeout_seconds: float = Field(default=5.0, gt=0)
    mailpit_from_email: EmailStr | None = None

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


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-level configuration."""

    return Settings()  # type: ignore[call-arg]
