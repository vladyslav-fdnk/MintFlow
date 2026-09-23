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

    assert "user:secret@" not in repr(settings)


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


def _base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr("postgresql://localhost/database"),
        "authentication_rate_limit_key": SecretStr("rate-secret"),
        "authentication_csrf_signing_key": SecretStr("csrf-secret"),
        "authentication_web_origin": "https://app.mintflow.test",
        "authentication_return_targets": frozenset({"dashboard"}),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


TELEGRAM = {
    "telegram_bot_token": SecretStr("123456:token-value-that-is-secret"),
    "telegram_bot_username": "mintflow_test_bot",
    "telegram_webhook_secret": SecretStr("webhook-secret_value"),
}


def test_telegram_is_disabled_without_configuration() -> None:
    assert _base_settings().telegram_enabled is False


def test_telegram_is_enabled_by_all_three_values_and_hides_secrets() -> None:
    settings = _base_settings(**TELEGRAM)

    assert settings.telegram_enabled is True
    assert "token-value-that-is-secret" not in repr(settings)
    assert "webhook-secret_value" not in repr(settings)


@pytest.mark.parametrize("missing", sorted(TELEGRAM))
def test_partial_telegram_configuration_is_rejected_without_echoing_secrets(missing: str) -> None:
    partial = {name: value for name, value in TELEGRAM.items() if name != missing}

    with pytest.raises(ValidationError, match="together") as error:
        _base_settings(**partial)

    assert "token-value-that-is-secret" not in str(error.value)
    assert "webhook-secret_value" not in str(error.value)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"telegram_bot_username": "bot"}, "username"),
        ({"telegram_bot_username": "mint flow bot"}, "username"),
        ({"telegram_webhook_secret": SecretStr("has spaces!")}, "webhook secret"),
        ({"telegram_webhook_secret": SecretStr("x" * 257)}, "webhook secret"),
    ],
)
def test_invalid_telegram_values_are_rejected(override: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message) as error:
        _base_settings(**{**TELEGRAM, **override})

    assert "has spaces!" not in str(error.value)
