"""Store today's exchange rates: ``python -m mintflow.commands.refresh_exchange_rates``.

Run daily from the scheduler (docs/exchange_rates_design.md, X2). The ECB comes first; the NBU
fills currencies the ECB does not publish (the hryvnia). A failed source keeps the rates it
would have replaced and makes the command exit 1.
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence

import httpx
from sqlalchemy.exc import SQLAlchemyError

from mintflow.application.rates import ExchangeRateSource, RefreshExchangeRates
from mintflow.config import get_settings
from mintflow.infrastructure.persistence import (
    SqlAlchemyExchangeRateRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.rates import EcbRateSource, NbuRateSource
from mintflow.logging import configure_logging

logger = logging.getLogger("mintflow.commands.refresh_exchange_rates")


def build_sources(client: httpx.Client) -> list[ExchangeRateSource]:
    return [EcbRateSource(client), NbuRateSource(client)]


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="Store today's exchange rates.").parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = None
    try:
        engine = create_database_engine(settings.database_url.get_secret_value())
        with create_session_factory(engine)() as session, httpx.Client() as client:
            result = RefreshExchangeRates(
                sources=build_sources(client),
                repository=SqlAlchemyExchangeRateRepository(session),
            ).execute()
    except SQLAlchemyError as error:
        logger.error("exchange_rates_refresh_failed error=%s", type(error).__name__)
        print("exchange rate refresh failed", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(
        json.dumps(
            {"saved": list(result.saved), "failed_sources": list(result.failed_sources)},
            sort_keys=True,
        )
    )
    return 1 if result.failed_sources or not result.saved else 0


if __name__ == "__main__":
    raise SystemExit(main())
