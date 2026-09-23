"""Azure AI Document Intelligence, prebuilt receipt model (receipt_recognition_design.md, R1).

Calls the REST API (api-version 2024-11-30) with httpx: submit the image, then
poll the operation until it finishes or the deadline passes. Every failure
becomes ``RecognitionUnavailable``, so the receipt falls back to manual entry.
Azure's types stay in this module; it returns only RCPT-03 candidates.

Currency is the risky field: Azure fills ``currencyCode`` from locale guesses
even when the receipt prints only "$" or nothing at all. So a currency
candidate is made only from what the total's own text shows: an ISO code, or a
symbol this module maps to exactly one currency. Dates whose printed form reads
both ways (``03/04/2026``) get both readings, and the selection step drops them.

Nothing here logs; the key, the image, and the response never reach a log or an
exception message.
"""

import re
import time
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from mintflow.application.receipts import (
    AmountLabel,
    CurrencyCandidate,
    CurrencyEvidence,
    DateCandidate,
    MerchantCandidate,
    RecognitionOutput,
    RecognitionUnavailable,
    TotalCandidate,
)
from mintflow.domain.capture import CurrencyCode

API_VERSION: Final = "2024-11-30"
ANALYZE_PATH: Final = "/documentintelligence/documentModels/prebuilt-receipt:analyze"
# Azure does not accept WebP; such receipts fall back to manual entry.
SUPPORTED_MEDIA_TYPES: Final = frozenset({"image/jpeg", "image/png"})
DEADLINE_SECONDS: Final = 45.0
REQUEST_TIMEOUT: Final = httpx.Timeout(10.0)
MIN_POLL_SECONDS: Final = 1.0
MAX_POLL_SECONDS: Final = 5.0

# Symbols that name exactly one currency. "$", "¥", "kr", and "lei" are left out on purpose.
_UNAMBIGUOUS_SYMBOLS: Final = {
    "€": "EUR",
    "£": "GBP",
    "₴": "UAH",
    "₽": "RUB",
    "zł": "PLN",
    "₹": "INR",
    "₺": "TRY",
    "₪": "ILS",
    "₸": "KZT",
    "₾": "GEL",
    "Kč": "CZK",
    "Ft": "HUF",
}
_AMBIGUOUS_SYMBOLS: Final = ("$", "¥", "kr")
_ISO_CODE: Final = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")
_NUMERIC_DATE: Final = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b")


class AzureReceiptRecognizer:
    def __init__(
        self,
        *,
        endpoint: str,
        key: SecretStr,
        client: httpx.Client | None = None,
        deadline_seconds: float = DEADLINE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("the Document Intelligence endpoint must be an https URL")
        self._endpoint = endpoint.rstrip("/")
        self._host = parts.hostname
        self._key = key
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
        self._deadline_seconds = deadline_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise RecognitionUnavailable("unsupported media type")
        deadline = self._monotonic() + self._deadline_seconds
        submitted = self._request(
            "POST",
            f"{self._endpoint}{ANALYZE_PATH}",
            params={"api-version": API_VERSION},
            content=image,
            headers={"Content-Type": media_type},
        )
        if submitted.status_code != 202:
            raise RecognitionUnavailable(f"analyze returned HTTP {submitted.status_code}")
        operation = self._operation_url(submitted)
        last = submitted
        while True:
            self._wait(last, deadline)
            polled = self._request("GET", operation)
            if polled.status_code != 200:
                raise RecognitionUnavailable(f"poll returned HTTP {polled.status_code}")
            try:
                body = polled.json()
            except ValueError:
                raise RecognitionUnavailable("poll returned invalid JSON") from None
            status = body.get("status") if isinstance(body, dict) else None
            if status == "succeeded":
                return parse_analyze_result(body)
            if status not in ("notStarted", "running"):
                raise RecognitionUnavailable("analysis did not succeed")
            last = polled

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = {**(headers or {}), "Ocp-Apim-Subscription-Key": self._key.get_secret_value()}
        try:
            return self._client.request(
                method, url, params=params, content=content, headers=headers
            )
        except httpx.HTTPError as error:
            # The exception text can carry the URL; only its type is kept.
            raise RecognitionUnavailable(f"request failed ({type(error).__name__})") from None

    def _operation_url(self, response: httpx.Response) -> str:
        location = str(response.headers.get("Operation-Location", ""))
        parts = urlsplit(location)
        # The key is sent to this URL, so it must be the configured Azure host.
        if parts.scheme != "https" or parts.hostname != self._host:
            raise RecognitionUnavailable("unexpected operation location")
        return location

    def _wait(self, response: httpx.Response, deadline: float) -> None:
        try:
            delay = float(response.headers.get("Retry-After", MIN_POLL_SECONDS))
        except ValueError:
            delay = MIN_POLL_SECONDS
        delay = min(max(delay, MIN_POLL_SECONDS), MAX_POLL_SECONDS)
        if self._monotonic() + delay > deadline:
            raise RecognitionUnavailable("deadline passed")
        self._sleep(delay)


# --- mapping ---------------------------------------------------------------------------------


def parse_analyze_result(body: Mapping[str, object]) -> RecognitionOutput:
    """Map a succeeded analyze operation to candidates; malformed fields are skipped."""
    result = body.get("analyzeResult")
    documents = result.get("documents") if isinstance(result, dict) else None
    if not isinstance(documents, list):
        raise RecognitionUnavailable("response has no documents")
    merchants: list[MerchantCandidate] = []
    dates: list[DateCandidate] = []
    totals: list[TotalCandidate] = []
    currencies: list[CurrencyCandidate] = []
    for document in documents:
        fields = document.get("fields") if isinstance(document, dict) else None
        if not isinstance(fields, dict):
            continue
        if (merchant := _merchant(fields.get("MerchantName"))) is not None:
            merchants.append(merchant)
        if (transaction_date := _date(fields.get("TransactionDate"))) is not None:
            dates.append(transaction_date)
        for name, label in (("Total", AmountLabel.TOTAL), ("Subtotal", AmountLabel.SUBTOTAL)):
            if (amount := _amount(fields.get(name), label)) is not None:
                totals.append(amount)
        if (currency := _currency(fields.get("Total"))) is not None:
            currencies.append(currency)
    return RecognitionOutput(
        merchants=tuple(merchants),
        dates=tuple(dates),
        totals=tuple(totals),
        currencies=tuple(currencies),
    )


def _confidence(field: Mapping[str, object]) -> float | None:
    confidence = field.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    return float(confidence) if 0 <= confidence <= 1 else None


def _merchant(field: object) -> MerchantCandidate | None:
    if not isinstance(field, dict) or (confidence := _confidence(field)) is None:
        return None
    text = field.get("valueString") or field.get("content")
    return MerchantCandidate(text, confidence) if isinstance(text, str) else None


def _date(field: object) -> DateCandidate | None:
    if not isinstance(field, dict) or (confidence := _confidence(field)) is None:
        return None
    try:
        value = date.fromisoformat(str(field.get("valueDate")))
    except ValueError:
        return None
    readings = {value}
    content = field.get("content")
    if isinstance(content, str) and (match := _NUMERIC_DATE.match(content)):
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        year = year + 2000 if year < 100 else year
        if first != second and first <= 12 and second <= 12:
            # Day and month cannot be told apart; offer both and let selection decide.
            for month, day in ((first, second), (second, first)):
                try:
                    readings.add(date(year, month, day))
                except ValueError:
                    continue
    return DateCandidate(tuple(sorted(readings)), confidence)


def _amount(field: object, label: AmountLabel) -> TotalCandidate | None:
    if not isinstance(field, dict) or (confidence := _confidence(field)) is None:
        return None
    currency = field.get("valueCurrency")
    amount = currency.get("amount") if isinstance(currency, dict) else field.get("valueNumber")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    try:
        # str() keeps the printed decimals; Decimal(float) would add binary noise.
        return TotalCandidate(Decimal(str(amount)), label, confidence)
    except InvalidOperation:
        return None


def _currency(field: object) -> CurrencyCandidate | None:
    """Currency only from what the total's printed text shows, never from Azure's guess."""
    if not isinstance(field, dict) or (confidence := _confidence(field)) is None:
        return None
    content = field.get("content")
    if not isinstance(content, str):
        return None
    value = field.get("valueCurrency")
    azure_code = value.get("currencyCode") if isinstance(value, dict) else None
    for code in _ISO_CODE.findall(content):
        # Upper-case words such as "VAT" are not currencies; only supported codes count.
        try:
            CurrencyCode(code)
        except ValueError:
            continue
        return CurrencyCandidate(code, CurrencyEvidence.EXPLICIT_CODE, confidence)
    for symbol, code in _UNAMBIGUOUS_SYMBOLS.items():
        if symbol in content:
            return CurrencyCandidate(code, CurrencyEvidence.UNAMBIGUOUS_SYMBOL, confidence)
    if any(symbol in content for symbol in _AMBIGUOUS_SYMBOLS):
        code = azure_code if isinstance(azure_code, str) else ""
        return CurrencyCandidate(code, CurrencyEvidence.AMBIGUOUS_SYMBOL, confidence)
    return None
