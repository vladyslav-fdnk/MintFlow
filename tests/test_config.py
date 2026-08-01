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
    )

    assert "secret" not in repr(settings)


def test_api_docs_are_disabled_by_default() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
    )

    assert settings.enable_api_docs is False


def test_authentication_rate_limit_key_is_required_and_secret() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate-secret"),
    )

    assert "rate-secret" not in repr(settings)


def test_authentication_rate_limit_key_cannot_be_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            database_url=SecretStr("postgresql://localhost/database"),
            _env_file=None,
        )
