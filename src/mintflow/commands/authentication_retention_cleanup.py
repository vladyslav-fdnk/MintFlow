import argparse
import json
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from mintflow.application.authentication.retention import CleanUpAuthenticationRetention
from mintflow.config import get_settings
from mintflow.infrastructure.persistence import create_database_engine, create_session_factory
from mintflow.infrastructure.persistence.authentication_retention import (
    PostgreSQLAuthenticationRetentionRepository,
)

DEFAULT_BATCH_SIZE = 1_000


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete expired authentication data in batches.")
    parser.add_argument("--batch-size", type=_positive_integer, default=DEFAULT_BATCH_SIZE)
    arguments = parser.parse_args(argv)

    engine = None
    try:
        settings = get_settings()
        engine = create_database_engine(settings.database_url.get_secret_value())
        with create_session_factory(engine)() as session:
            result = CleanUpAuthenticationRetention(
                repository=PostgreSQLAuthenticationRetentionRepository(session)
            ).execute(batch_size=arguments.batch_size)
        print(json.dumps(result.aggregate_counts(), sort_keys=True))
    except (SQLAlchemyError, TypeError, ValueError):
        print("authentication retention cleanup failed", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
