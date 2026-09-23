import os

import pytest
from pydantic import SecretStr

from mintflow.config import Settings

_SETTINGS_ENV_PREFIX = "MINTFLOW_"
_TEST_ONLY_ENV_PREFIX = "MINTFLOW_TEST_"


@pytest.fixture(autouse=True)
def _isolate_settings_from_the_local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's .env file and exported MINTFLOW_* variables out of every test.

    Settings reads both by default, so without this the suite depends on the
    machine it runs on. MINTFLOW_TEST_* variables are kept: they configure the
    tests themselves (for example the PostgreSQL integration database) and are
    not Settings fields.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith(_SETTINGS_ENV_PREFIX) and not upper.startswith(_TEST_ONLY_ENV_PREFIX):
            monkeypatch.delenv(name)


@pytest.fixture
def settings() -> Settings:
    """Provide isolated application settings for tests."""

    return Settings(
        environment="test",
        log_level="CRITICAL",
        database_url=SecretStr("postgresql://test:test@localhost:5432/test"),
        authentication_rate_limit_key=SecretStr("test-rate-limit-key"),
        authentication_csrf_signing_key=SecretStr("test-csrf-signing-key"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
        email_backend="mailpit",
        mailpit_smtp_host="mailpit",
        mailpit_from_email="no-reply@mintflow.dev",
    )


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on the application's asyncio backend."""

    return "asyncio"
