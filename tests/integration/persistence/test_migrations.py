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
        assert "login_challenges" not in inspect(engine).get_table_names()
        assert "authentication_rate_limit_buckets" not in inspect(engine).get_table_names()
        assert "web_sessions" not in inspect(engine).get_table_names()
        assert "authentication_audit_records" not in inspect(engine).get_table_names()
        assert "categories" not in inspect(engine).get_table_names()
        assert "capture_drafts" not in inspect(engine).get_table_names()
        assert "expenses" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sqlalchemy_database_url(migrated_database_url))
    try:
        assert {
            "users",
            "email_identities",
            "login_challenges",
            "authentication_rate_limit_buckets",
            "web_sessions",
            "authentication_audit_records",
            "categories",
            "capture_drafts",
            "expenses",
        }.issubset(inspect(engine).get_table_names())
    finally:
        engine.dispose()
