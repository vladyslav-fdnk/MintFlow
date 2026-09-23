import logging
import os
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

import mintflow.main
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


@pytest.fixture
def app_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pytest.LogCaptureFixture]:
    """caplog that still sees every record after ``create_app`` configures logging.

    ``configure_logging`` runs ``basicConfig(force=True)``, which detaches caplog's root
    handler, and test settings use CRITICAL, which would silence the records anyway.
    Assertions that a secret is absent from the logs are only meaningful with both
    undone, so this re-attaches the handler and lowers the levels to DEBUG after the
    application configures logging, and restores the levels afterwards.
    """
    root = logging.getLogger()
    project = logging.getLogger("mintflow")
    saved_levels = (root.level, project.level)
    configure = mintflow.main.configure_logging

    def configure_and_capture(level: str) -> None:
        configure(level)
        root.addHandler(caplog.handler)
        root.setLevel(logging.DEBUG)
        project.setLevel(logging.DEBUG)

    monkeypatch.setattr(mintflow.main, "configure_logging", configure_and_capture)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        root.removeHandler(caplog.handler)
        root.setLevel(saved_levels[0])
        project.setLevel(saved_levels[1])
