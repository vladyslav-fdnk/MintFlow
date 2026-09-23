from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from mintflow.config import get_settings
from mintflow.infrastructure.persistence.database import sqlalchemy_database_url
from mintflow.infrastructure.persistence.models import Base

config = context.config

if config.config_file_name is not None:
    # Keep loggers created before migrations run (for example by the application when
    # tests migrate in-process); fileConfig would otherwise disable them silently.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    configured_url = config.attributes.get("database_url")
    if isinstance(configured_url, str):
        return sqlalchemy_database_url(configured_url)
    return sqlalchemy_database_url(get_settings().database_url.get_secret_value())


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
