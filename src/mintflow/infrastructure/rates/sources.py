"""ECB and NBU daily exchange rates (docs/exchange_rates_design.md, X2).

Both feeds are public, free, and keyless. Parsing is strict: anything unexpected is a failed
source, never a guessed rate. Responses are size-limited before parsing.
"""

import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from xml.etree import ElementTree

import httpx

from mintflow.application.rates import ExchangeRate, ExchangeRateSourceFailed
from mintflow.domain.capture import CurrencyCode

ECB_DAILY_URL: Final = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
NBU_DAILY_URL: Final = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"
TIMEOUT: Final = httpx.Timeout(15.0)
MAX_RESPONSE_BYTES: Final = 256 * 1024
_ECB_NAMESPACE: Final = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"


def _decimal(text: object) -> Decimal:
    try:
        value = Decimal(str(text))
    except InvalidOperation:
        raise ExchangeRateSourceFailed("not a number") from None
    if not value.is_finite() or value <= 0:
        raise ExchangeRateSourceFailed("not a positive rate")
    return value


def _currency(code: object) -> CurrencyCode | None:
    """A catalogue currency, or None for one MintFlow does not support."""
    try:
        return CurrencyCode(str(code))
    except ValueError:
        return None


def parse_ecb(body: bytes) -> list[ExchangeRate]:
    """Units per euro for every catalogue currency in the ECB daily file."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise ExchangeRateSourceFailed("ECB: not XML") from None
    day = root.find(f"./{_ECB_NAMESPACE}Cube/{_ECB_NAMESPACE}Cube[@time]")
    if day is None:
        raise ExchangeRateSourceFailed("ECB: no dated rates")
    try:
        rate_date = date.fromisoformat(day.attrib["time"])
    except ValueError:
        raise ExchangeRateSourceFailed("ECB: bad date") from None
    rates = []
    for entry in day.findall(f"{_ECB_NAMESPACE}Cube"):
        currency = _currency(entry.attrib.get("currency"))
        if currency is not None:
            rates.append(
                ExchangeRate(currency, _decimal(entry.attrib.get("rate")), rate_date, "ECB")
            )
    if not rates:
        raise ExchangeRateSourceFailed("ECB: no rates")
    return rates


def parse_nbu(body: bytes) -> list[ExchangeRate]:
    """Units per euro derived from the NBU's hryvnias per unit, through its euro rate."""
    try:
        entries = json.loads(body)
    except ValueError:
        raise ExchangeRateSourceFailed("NBU: not JSON") from None
    if not isinstance(entries, list):
        raise ExchangeRateSourceFailed("NBU: unexpected shape")
    hryvnias: dict[str, tuple[Decimal, date]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ExchangeRateSourceFailed("NBU: unexpected entry")
        try:
            day = datetime.strptime(str(entry["exchangedate"]), "%d.%m.%Y").date()
            hryvnias[str(entry["cc"])] = (_decimal(entry["rate"]), day)
        except (KeyError, ValueError):
            raise ExchangeRateSourceFailed("NBU: unexpected entry") from None
    if "EUR" not in hryvnias:
        raise ExchangeRateSourceFailed("NBU: no euro rate")
    uah_per_eur, eur_day = hryvnias["EUR"]
    rates = [ExchangeRate(CurrencyCode("UAH"), uah_per_eur, eur_day, "NBU")]
    for code, (uah_per_unit, day) in sorted(hryvnias.items()):
        currency = _currency(code)
        if currency is None or code in {"EUR", "UAH"}:
            continue
        rates.append(ExchangeRate(currency, uah_per_eur / uah_per_unit, day, "NBU"))
    return rates


def _download(client: httpx.Client, url: str, name: str) -> bytes:
    try:
        with client.stream("GET", url, timeout=TIMEOUT) as response:
            if response.status_code != 200:
                raise ExchangeRateSourceFailed(f"{name}: HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ExchangeRateSourceFailed(f"{name}: response too large")
            return bytes(body)
    except httpx.HTTPError as error:
        raise ExchangeRateSourceFailed(f"{name}: {type(error).__name__}") from None


class EcbRateSource:
    name = "ECB"

    def __init__(self, client: httpx.Client, url: str = ECB_DAILY_URL) -> None:
        self._client = client
        self._url = url

    def fetch(self) -> Sequence[ExchangeRate]:
        return parse_ecb(_download(self._client, self._url, self.name))


class NbuRateSource:
    name = "NBU"

    def __init__(self, client: httpx.Client, url: str = NBU_DAILY_URL) -> None:
        self._client = client
        self._url = url

    def fetch(self) -> Sequence[ExchangeRate]:
        return parse_nbu(_download(self._client, self._url, self.name))
