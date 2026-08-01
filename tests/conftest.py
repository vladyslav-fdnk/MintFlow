import pytest
from pydantic import SecretStr

from mintflow.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Provide isolated application settings for tests."""

    return Settings(
        environment="test",
        log_level="CRITICAL",
        database_url=SecretStr("postgresql://test:test@localhost:5432/test"),
        authentication_rate_limit_key=SecretStr("test-rate-limit-key"),
    )


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on the application's asyncio backend."""

    return "asyncio"
