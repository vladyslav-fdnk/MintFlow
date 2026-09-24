from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from mintflow.application.rates import ExchangeRateSourceFailed
from mintflow.infrastructure.rates import EcbRateSource, NbuRateSource, parse_ecb, parse_nbu
from mintflow.infrastructure.rates.sources import MAX_RESPONSE_BYTES

FIXTURES = Path(__file__).parent / "fixtures"
ECB = (FIXTURES / "ecb_daily.xml").read_bytes()
NBU = (FIXTURES / "nbu_daily.json").read_bytes()


def test_the_ecb_file_gives_catalogue_currencies_per_euro() -> None:
    rates = {rate.currency.value: rate for rate in parse_ecb(ECB)}

    # Only currencies MintFlow supports; the hryvnia and the Bahraini dinar are not published.
    assert set(rates) == {"USD", "JPY", "GBP", "PLN"}
    assert rates["USD"].units_per_eur == Decimal("1.1411")
    assert {rate.rate_date for rate in rates.values()} == {date(2026, 9, 23)}
    assert {rate.source for rate in rates.values()} == {"ECB"}


def test_the_nbu_file_gives_the_hryvnia_and_cross_rates_through_the_euro() -> None:
    rates = {rate.currency.value: rate for rate in parse_nbu(NBU)}

    assert rates["UAH"].units_per_eur == Decimal("51.1864")
    # Hryvnias per euro divided by hryvnias per dollar: close to the ECB's own USD rate.
    assert abs(rates["USD"].units_per_eur - Decimal("1.1411")) < Decimal("0.001")
    assert "BHD" not in rates and "EUR" not in rates
    assert rates["UAH"].rate_date == date(2026, 9, 24)


ECB_HEAD = (
    b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
    b'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube>'
)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not xml", id="not xml"),
        pytest.param(ECB_HEAD + b"</Cube></gesmes:Envelope>", id="no dated rates"),
        pytest.param(
            ECB_HEAD + b"<Cube time='2026-09-23'><Cube currency='USD' rate='-1'/></Cube></Cube>"
            b"</gesmes:Envelope>",
            id="negative rate",
        ),
        pytest.param(
            ECB_HEAD + b"<Cube time='yesterday'><Cube currency='USD' rate='1.1'/></Cube></Cube>"
            b"</gesmes:Envelope>",
            id="bad date",
        ),
        pytest.param(
            ECB_HEAD + b"<Cube time='2026-09-23'><Cube currency='CHF' rate='0.9'/></Cube></Cube>"
            b"</gesmes:Envelope>",
            id="no catalogue currency",
        ),
    ],
)
def test_malformed_ecb_files_fail_the_source(body: bytes) -> None:
    with pytest.raises(ExchangeRateSourceFailed):
        parse_ecb(body)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"{", id="not json"),
        pytest.param(b"{}", id="not a list"),
        pytest.param(b'[{"cc": "USD", "rate": 44.8, "exchangedate": "24.09.2026"}]', id="no euro"),
        pytest.param(
            b'[{"cc": "EUR", "rate": "abc", "exchangedate": "24.09.2026"}]', id="bad rate"
        ),
        pytest.param(b'[{"cc": "EUR", "rate": 51.1, "exchangedate": "2026-09-24"}]', id="bad date"),
        pytest.param(b'["EUR"]', id="bad entry"),
    ],
)
def test_malformed_nbu_files_fail_the_source(body: bytes) -> None:
    with pytest.raises(ExchangeRateSourceFailed):
        parse_nbu(body)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_sources_download_and_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ECB if "ecb" in request.url.host else NBU)

    client = _client(httpx.MockTransport(handler))

    assert {rate.currency.value for rate in EcbRateSource(client).fetch()} == {
        "USD",
        "JPY",
        "GBP",
        "PLN",
    }
    assert "UAH" in {rate.currency.value for rate in NbuRateSource(client).fetch()}


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(httpx.Response(503), id="server error"),
        pytest.param(httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)), id="too large"),
    ],
)
def test_failed_downloads_fail_the_source(response: httpx.Response) -> None:
    source = EcbRateSource(_client(httpx.MockTransport(lambda request: response)))

    with pytest.raises(ExchangeRateSourceFailed):
        source.fetch()


def test_transport_errors_fail_the_source_by_type_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused by https://www.ecb.europa.eu")

    with pytest.raises(ExchangeRateSourceFailed) as error:
        NbuRateSource(_client(httpx.MockTransport(handler))).fetch()

    assert str(error.value) == "NBU: ConnectError"
    assert error.value.__cause__ is None
