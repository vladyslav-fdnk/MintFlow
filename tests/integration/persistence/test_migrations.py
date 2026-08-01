import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from mintflow.infrastructure.persistence.database import sqlalchemy_database_url

pytestmark = pytest.mark.integration


def test_migration_downgrades_and_reupgrades_cleanly(migrated_database_url: str) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = migrated_database_url

    command.downgrade(config, "base")
    engine = create_engine(sqlalchemy_database_url(migrated_database_url))
    try:
        assert "users" not in inspect(engine).get_table_names()
        assert "email_identities" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sqlalchemy_database_url(migrated_database_url))
    try:
        assert {"users", "email_identities"}.issubset(inspect(engine).get_table_names())
    finally:
        engine.dispose()
