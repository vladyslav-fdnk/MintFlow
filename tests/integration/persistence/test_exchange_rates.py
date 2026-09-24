import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mintflow.application.rates import ExchangeRate, ExchangeRateSourceFailed
from mintflow.commands import refresh_exchange_rates
from mintflow.config import get_settings
from mintflow.domain.capture import CurrencyCode
from mintflow.infrastructure.persistence import SqlAlchemyExchangeRateRepository
from mintflow.infrastructure.persistence.models import ExchangeRateRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 24, 17, 0, tzinfo=UTC)


def _rate(
    code: str, units: str, day: date = date(2026, 9, 23), source: str = "ECB"
) -> ExchangeRate:
    return ExchangeRate(CurrencyCode(code), Decimal(units), day, source)


def test_rates_round_trip_with_full_precision(db_session: Session) -> None:
    repository = SqlAlchemyExchangeRateRepository(db_session)

    repository.save([_rate("USD", "1.1411"), _rate("UAH", "51.1864", source="NBU")], fetched_at=NOW)

    latest = repository.latest()
    assert latest.rates["USD"].units_per_eur == Decimal("1.1411")
    assert latest.rates["UAH"].source == "NBU"
    assert latest.convert(1_000, CurrencyCode("EUR"), CurrencyCode("USD")) == 1_141


def test_a_refresh_replaces_its_currencies_and_keeps_the_others(db_session: Session) -> None:
    repository = SqlAlchemyExchangeRateRepository(db_session)
    repository.save([_rate("USD", "1.10"), _rate("PLN", "4.30")], fetched_at=NOW)

    repository.save([_rate("USD", "1.20", date(2026, 9, 24))], fetched_at=NOW)

    latest = repository.latest()
    assert latest.rates["USD"].units_per_eur == Decimal("1.20")
    assert latest.rates["USD"].rate_date == date(2026, 9, 24)
    assert latest.rates["PLN"].units_per_eur == Decimal("4.30")
    assert (
        db_session.scalar(
            select(ExchangeRateRecord.currency).where(ExchangeRateRecord.currency == "USD")
        )
        == "USD"
    )


class FakeSource:
    def __init__(self, name: str, rates: list[ExchangeRate] | None) -> None:
        self.name = name
        self._rates = rates

    def fetch(self) -> list[ExchangeRate]:
        if self._rates is None:
            raise ExchangeRateSourceFailed(f"{self.name}: down")
        return self._rates


def _run_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_url: str,
    sources: list[FakeSource],
) -> tuple[int, dict[str, list[str]]]:
    for name, value in {
        "MINTFLOW_DATABASE_URL": database_url,
        "MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY": "rate",
        "MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY": "csrf",
        "MINTFLOW_AUTHENTICATION_WEB_ORIGIN": "https://app.mintflow.test",
        "MINTFLOW_AUTHENTICATION_RETURN_TARGETS": '["dashboard"]',
        "MINTFLOW_LOG_LEVEL": "CRITICAL",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(refresh_exchange_rates, "build_sources", lambda client: sources)
    get_settings.cache_clear()
    try:
        code = refresh_exchange_rates.main([])
    finally:
        get_settings.cache_clear()
    return code, json.loads(capsys.readouterr().out)


def test_the_command_stores_rates_and_reports_them(
    db_session: Session,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output = _run_command(
        monkeypatch,
        capsys,
        migrated_database_url,
        [
            FakeSource("ECB", [_rate("USD", "1.14")]),
            FakeSource("NBU", [_rate("UAH", "51", source="NBU")]),
        ],
    )

    assert (code, output) == (0, {"saved": ["UAH", "USD"], "failed_sources": []})
    assert set(SqlAlchemyExchangeRateRepository(db_session).latest().rates) == {"UAH", "USD"}


def test_a_failed_source_exits_non_zero_and_keeps_old_rates(
    db_session: Session,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    SqlAlchemyExchangeRateRepository(db_session).save([_rate("USD", "1.10")], fetched_at=NOW)

    code, output = _run_command(
        monkeypatch,
        capsys,
        migrated_database_url,
        [FakeSource("ECB", None), FakeSource("NBU", [_rate("UAH", "51", source="NBU")])],
    )

    assert (code, output) == (1, {"saved": ["UAH"], "failed_sources": ["ECB"]})
    db_session.expire_all()
    latest = SqlAlchemyExchangeRateRepository(db_session).latest()
    assert latest.rates["USD"].units_per_eur == Decimal("1.10")
    assert "UAH" in latest.rates


def test_the_real_sources_are_built_for_the_command() -> None:
    names = [source.name for source in refresh_exchange_rates.build_sources(httpx.Client())]

    assert names == ["ECB", "NBU"]
