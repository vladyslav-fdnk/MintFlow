import pytest
from pydantic import SecretStr, ValidationError

from mintflow.config import Settings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINTFLOW_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_database_url_is_not_exposed() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://user:secret@localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
        authentication_csrf_signing_key=SecretStr("csrf-secret"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
    )

    assert "secret" not in repr(settings)


def test_api_docs_are_disabled_by_default() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
        authentication_csrf_signing_key=SecretStr("csrf-secret"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
    )

    assert settings.enable_api_docs is False


def test_authentication_rate_limit_key_is_required_and_secret() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
        authentication_csrf_signing_key=SecretStr("csrf-secret"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
    )

    assert "rate-secret" not in repr(settings)


def test_authentication_rate_limit_key_cannot_be_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            database_url=SecretStr("postgresql://localhost/database"),
            authentication_web_origin="https://app.mintflow.test",
            authentication_return_targets=frozenset({"dashboard"}),
            _env_file=None,
        )


def test_authentication_csrf_signing_key_is_required_and_secret() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
        authentication_csrf_signing_key=SecretStr("csrf-secret"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
    )

    assert "csrf-secret" not in repr(settings)


def test_authentication_csrf_signing_key_cannot_be_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            database_url=SecretStr("postgresql://localhost/database"),
            authentication_rate_limit_key=SecretStr("rate-secret"),
            authentication_web_origin="https://app.mintflow.test",
            authentication_return_targets=frozenset({"dashboard"}),
            _env_file=None,
        )


def test_authentication_http_configuration_is_required_and_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            database_url=SecretStr("postgresql://localhost/database"),
            authentication_rate_limit_key=SecretStr("rate-secret"),
            authentication_csrf_signing_key=SecretStr("csrf-secret"),
            _env_file=None,
        )

    with pytest.raises(ValidationError, match="at least one authentication return target"):
        Settings(
            database_url=SecretStr("postgresql://localhost/database"),
            authentication_rate_limit_key=SecretStr("rate-secret"),
            authentication_csrf_signing_key=SecretStr("csrf-secret"),
            authentication_web_origin="https://app.mintflow.test",
            authentication_return_targets=frozenset(),
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_mailpit_is_rejected_outside_local_or_test_environments(environment: str) -> None:
    with pytest.raises(ValidationError, match="Mailpit email delivery is allowed only"):
        Settings(
            environment=environment,  # type: ignore[arg-type]
            database_url=SecretStr("postgresql://localhost/database"),
            authentication_rate_limit_key=SecretStr("rate-secret"),
            authentication_csrf_signing_key=SecretStr("csrf-secret"),
            authentication_web_origin="https://app.mintflow.test",
            authentication_return_targets=frozenset({"dashboard"}),
            email_backend="mailpit",
            mailpit_smtp_host="mailpit",
            mailpit_from_email="no-reply@mintflow.dev",
        )


def test_mailpit_requires_explicit_backend_and_connection_values() -> None:
    settings = Settings(
        environment="production",
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
        authentication_csrf_signing_key=SecretStr("csrf-secret"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
    )

    assert settings.email_backend is None

    with pytest.raises(ValidationError, match="requires an SMTP host and sender address"):
        Settings(
            environment="development",
            database_url=SecretStr("postgresql://localhost/database"),
            authentication_rate_limit_key=SecretStr("rate-secret"),
            authentication_csrf_signing_key=SecretStr("csrf-secret"),
            authentication_web_origin="https://app.mintflow.test",
            authentication_return_targets=frozenset({"dashboard"}),
            email_backend="mailpit",
        )
