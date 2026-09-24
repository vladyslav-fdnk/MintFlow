"""The latest exchange rate per currency (docs/exchange_rates_design.md, X2)."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mintflow.application.rates import ExchangeRate, ExchangeRates
from mintflow.domain.capture import CurrencyCode
from mintflow.infrastructure.persistence.models import ExchangeRateRecord


class SqlAlchemyExchangeRateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, rates: Sequence[ExchangeRate], *, fetched_at: datetime) -> None:
        """Replace the stored rate of each given currency; other currencies keep theirs."""
        if not rates:
            return
        statement = insert(ExchangeRateRecord).values(
            [
                {
                    "currency": rate.currency.value,
                    "units_per_eur": rate.units_per_eur,
                    "rate_date": rate.rate_date,
                    "source": rate.source,
                    "fetched_at": fetched_at,
                }
                for rate in rates
            ]
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[ExchangeRateRecord.currency],
                set_={
                    "units_per_eur": statement.excluded.units_per_eur,
                    "rate_date": statement.excluded.rate_date,
                    "source": statement.excluded.source,
                    "fetched_at": statement.excluded.fetched_at,
                },
            )
        )
        self._session.commit()

    def latest(self) -> ExchangeRates:
        rates: dict[str, ExchangeRate] = {}
        for record in self._session.scalars(select(ExchangeRateRecord)):
            try:
                currency = CurrencyCode(record.currency)
            except ValueError:
                continue  # a currency no longer in the catalogue
            rates[currency.value] = ExchangeRate(
                currency, record.units_per_eur, record.rate_date, record.source
            )
        return ExchangeRates(rates)
