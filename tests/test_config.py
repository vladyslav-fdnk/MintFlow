import pytest
from pydantic import SecretStr, ValidationError

from mintflow.config import Settings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINTFLOW_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_database_url_is_not_exposed() -> None:
    settings = Settings(database_url=SecretStr("postgresql://user:secret@localhost/database"))

    assert "secret" not in repr(settings)


def test_api_docs_are_disabled_by_default() -> None:
    settings = Settings(database_url=SecretStr("postgresql://localhost/database"))

    assert settings.enable_api_docs is False
