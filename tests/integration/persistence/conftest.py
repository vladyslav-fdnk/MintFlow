import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from mintflow.infrastructure.persistence import create_database_engine, create_session_factory


def _test_database_url() -> str:
    database_url = os.getenv("MINTFLOW_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("MINTFLOW_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database_name.endswith("_test"):
        pytest.fail("MINTFLOW_TEST_DATABASE_URL must name a database ending in '_test'")
    return database_url


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    database_url = _test_database_url()
    config = _alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield database_url
    command.downgrade(config, "base")


@pytest.fixture
def engine(migrated_database_url: str) -> Iterator[Engine]:
    database_engine = create_database_engine(migrated_database_url)
    yield database_engine
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE authentication_audit_records, "
                "authentication_rate_limit_buckets, web_sessions, "
                "login_challenges, email_identities, capture_drafts, users"
            )
        )
    database_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    session = create_session_factory(engine)()
    yield session
    session.rollback()
    session.close()
