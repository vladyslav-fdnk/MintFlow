import os
from pathlib import Path

import pytest

from mintflow.config import Settings

REQUIRED = {
    "MINTFLOW_DATABASE_URL": "postgresql://isolated/database",
    "MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY": "rate-secret",
    "MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY": "csrf-secret",
    "MINTFLOW_AUTHENTICATION_WEB_ORIGIN": "https://app.mintflow.test",
    "MINTFLOW_AUTHENTICATION_RETURN_TARGETS": '["dashboard"]',
}


def test_a_dotenv_file_in_the_working_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MINTFLOW_ENABLE_API_DOCS=true\nMINTFLOW_LOG_LEVEL=DEBUG\n")
    monkeypatch.chdir(tmp_path)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings()  # type: ignore[call-arg]

    assert settings.enable_api_docs is False
    assert settings.log_level == "INFO"


def test_exported_settings_variables_are_removed_before_each_test() -> None:
    leaked = [
        name
        for name in os.environ
        if name.upper().startswith("MINTFLOW_") and not name.upper().startswith("MINTFLOW_TEST_")
    ]

    assert leaked == []
