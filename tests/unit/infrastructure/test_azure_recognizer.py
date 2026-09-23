import copy
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from mintflow.application.receipts import (
    AmountLabel,
    CurrencyCandidate,
    CurrencyEvidence,
    RecognitionUnavailable,
    TotalCandidate,
    select_values,
)
from mintflow.domain.capture import CurrencyCode, MerchantName, Money, TransactionDate
from mintflow.domain.user import Timezone
from mintflow.infrastructure.recognition.azure import (
    AzureReceiptRecognizer,
    parse_analyze_result,
)

ENDPOINT = "https://sample.cognitiveservices.azure.com"
KEY = "azure-key-that-must-stay-secret"
OPERATION = f"{ENDPOINT}/documentintelligence/documentModels/prebuilt-receipt/analyzeResults/op-1"
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
JPEG = b"\xff\xd8\xff\xe0image"
SUCCEEDED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "azure_receipt_succeeded.json").read_text()
)


def _with_fields(**fields: object) -> dict[str, Any]:
    body = copy.deepcopy(SUCCEEDED)
    document_fields = body["analyzeResult"]["documents"][0]["fields"]
    for name, value in fields.items():
        if value is None:
            document_fields.pop(name, None)
        else:
            document_fields[name] = value
    return body


def _total(content: str, amount: float = 12.5, code: str | None = "EUR") -> dict[str, object]:
    value: dict[str, object] = {"amount": amount}
    if code is not None:
        value["currencyCode"] = code
    return {"type": "currency", "valueCurrency": value, "content": content, "confidence": 0.96}


def _selected(body: dict[str, Any]) -> tuple[object, object, object]:
    values = select_values(parse_analyze_result(body), now=NOW, timezone=Timezone("UTC"))
    return values.merchant, values.transaction_date, values.total


# --- mapping recorded responses ---------------------------------------------------------------


def test_a_full_receipt_maps_to_every_field() -> None:
    output = parse_analyze_result(SUCCEEDED)

    assert output.totals == (
        TotalCandidate(Decimal("12.5"), AmountLabel.TOTAL, 0.96),
        TotalCandidate(Decimal("10.5"), AmountLabel.SUBTOTAL, 0.9),
    )
    assert output.currencies == (CurrencyCandidate("EUR", CurrencyEvidence.EXPLICIT_CODE, 0.96),)
    assert _selected(SUCCEEDED) == (
        MerchantName("Sample Market"),
        TransactionDate(date(2026, 9, 20)),
        Money(minor_units=1250, currency=CurrencyCode("EUR")),
    )


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        pytest.param(_total("€12.50"), "EUR", id="euro sign"),
        pytest.param(_total("12,50 zł", code="PLN"), "PLN", id="zloty sign"),
        pytest.param(_total("12.50 VAT EUR"), "EUR", id="VAT is not a currency"),
        pytest.param(_total("$12.50", code="USD"), None, id="dollar sign is ambiguous"),
        pytest.param(_total("12.50", code="USD"), None, id="Azure's locale guess is ignored"),
        pytest.param(_total("12.50", code=None), None, id="no currency at all"),
    ],
)
def test_currency_comes_only_from_the_printed_total(
    total: dict[str, object], expected: str | None
) -> None:
    _, _, money = _selected(_with_fields(Total=total))

    if expected is None:
        assert money is None
    else:
        assert money == Money(minor_units=1250, currency=CurrencyCode(expected))


@pytest.mark.parametrize(
    ("content", "value", "readings"),
    [
        pytest.param("03/04/2026", "2026-03-04", 2, id="day and month ambiguous"),
        pytest.param("20.09.2026", "2026-09-20", 1, id="day above 12"),
        pytest.param("05.05.26", "2026-05-05", 1, id="same day and month"),
        pytest.param("Sep 20, 2026", "2026-09-20", 1, id="written month"),
    ],
)
def test_ambiguous_printed_dates_offer_both_readings(
    content: str, value: str, readings: int
) -> None:
    body = _with_fields(
        TransactionDate={
            "type": "date",
            "valueDate": value,
            "content": content,
            "confidence": 0.97,
        }
    )

    [candidate] = parse_analyze_result(body).dates
    assert len(candidate.readings) == readings


def test_a_partial_response_keeps_what_it_has() -> None:
    body = _with_fields(Total=None, Subtotal=None, MerchantName=None)

    assert _selected(body) == (None, TransactionDate(date(2026, 9, 20)), None)


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"type": "string", "valueString": "Shop"}, id="no confidence"),
        pytest.param(
            {"type": "string", "valueString": "Shop", "confidence": 7}, id="bad confidence"
        ),
        pytest.param({"type": "string", "valueString": 42, "confidence": 0.9}, id="not text"),
        pytest.param("Shop", id="not an object"),
    ],
)
def test_malformed_fields_are_skipped(field: object) -> None:
    body = _with_fields(
        MerchantName=field,
        Total={"type": "currency", "valueCurrency": {"amount": "12.50"}, "confidence": 0.9},
        TransactionDate={"type": "date", "valueDate": "20/09", "confidence": 0.9},
    )

    output = parse_analyze_result(body)

    assert output.merchants == ()
    assert output.dates == ()
    assert [total.label for total in output.totals] == [AmountLabel.SUBTOTAL]


def test_two_receipts_with_different_totals_leave_the_total_empty() -> None:
    body = copy.deepcopy(SUCCEEDED)
    second = copy.deepcopy(body["analyzeResult"]["documents"][0])
    second["fields"]["Total"] = _total("20,00 EUR", amount=20.0)
    body["analyzeResult"]["documents"].append(second)

    assert _selected(body)[2] is None


def test_a_response_without_documents_is_unavailable() -> None:
    with pytest.raises(RecognitionUnavailable):
        parse_analyze_result({"status": "succeeded", "analyzeResult": {}})


# --- the HTTP exchange ------------------------------------------------------------------------


class Azure:
    """A scripted Azure endpoint: one analyze response, then poll responses in order."""

    def __init__(self, analyze: httpx.Response, polls: list[httpx.Response]) -> None:
        self.analyze = analyze
        self.polls = polls
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            return self.analyze
        return self.polls.pop(0)


def _accepted(location: str = OPERATION) -> httpx.Response:
    return httpx.Response(202, headers={"Operation-Location": location, "Retry-After": "1"})


def _running() -> httpx.Response:
    return httpx.Response(200, json={"status": "running"}, headers={"Retry-After": "2"})


def _recognizer(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeps: list[float] | None = None,
    deadline_seconds: float = 45.0,
) -> AzureReceiptRecognizer:
    clock = [0.0]
    recorded = sleeps if sleeps is not None else []

    def sleep(seconds: float) -> None:
        recorded.append(seconds)
        clock[0] += seconds

    return AzureReceiptRecognizer(
        endpoint=ENDPOINT + "/",
        key=SecretStr(KEY),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        deadline_seconds=deadline_seconds,
        sleep=sleep,
        monotonic=lambda: clock[0],
    )


def test_a_receipt_is_submitted_then_polled_until_it_succeeds() -> None:
    azure = Azure(_accepted(), [_running(), httpx.Response(200, json=SUCCEEDED)])
    sleeps: list[float] = []

    output = _recognizer(azure, sleeps=sleeps).recognize(image=JPEG, media_type="image/jpeg")

    assert output == parse_analyze_result(SUCCEEDED)
    submit, *polls = azure.requests
    assert submit.url.path == "/documentintelligence/documentModels/prebuilt-receipt:analyze"
    assert submit.url.params["api-version"] == "2024-11-30"
    assert submit.headers["Content-Type"] == "image/jpeg"
    assert submit.content == JPEG
    assert [str(poll.url) for poll in polls] == [OPERATION, OPERATION]
    assert all(r.headers["Ocp-Apim-Subscription-Key"] == KEY for r in azure.requests)
    assert sleeps == [1.0, 2.0]  # Retry-After is honoured


def _failure(
    handler: Callable[[httpx.Request], httpx.Response], **options: Any
) -> RecognitionUnavailable:
    with pytest.raises(RecognitionUnavailable) as error:
        _recognizer(handler, **options).recognize(image=JPEG, media_type="image/jpeg")
    assert KEY not in str(error.value) and error.value.__cause__ is None
    return error.value


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_a_refused_submission_is_unavailable(status: int) -> None:
    _failure(Azure(httpx.Response(status, json={"error": {"code": "Denied"}}), []))


@pytest.mark.parametrize(
    "poll",
    [
        pytest.param(httpx.Response(200, json={"status": "failed"}), id="analysis failed"),
        pytest.param(httpx.Response(200, content=b"not json"), id="invalid json"),
        pytest.param(httpx.Response(404), id="operation gone"),
        pytest.param(httpx.Response(200, json=[]), id="unexpected shape"),
    ],
)
def test_a_failed_poll_is_unavailable(poll: httpx.Response) -> None:
    _failure(Azure(_accepted(), [poll]))


def test_the_key_is_never_sent_to_another_host() -> None:
    azure = Azure(_accepted("https://attacker.example/analyzeResults/op-1"), [])

    _failure(azure)

    assert [request.method for request in azure.requests] == ["POST"]


def test_polling_stops_at_the_deadline() -> None:
    azure = Azure(_accepted(), [_running() for _ in range(10)])

    _failure(azure, deadline_seconds=4.0)

    assert len(azure.requests) <= 3


def test_transport_errors_do_not_leak_the_key_or_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    error = _failure(handler)

    assert ENDPOINT not in str(error)


def test_webp_is_not_sent_to_azure() -> None:
    azure = Azure(_accepted(), [])

    with pytest.raises(RecognitionUnavailable):
        _recognizer(azure).recognize(image=b"RIFF", media_type="image/webp")
    assert azure.requests == []


@pytest.mark.parametrize(
    "endpoint", ["http://sample.cognitiveservices.azure.com", "https://", "https://h/?x=1"]
)
def test_only_https_endpoints_are_accepted(endpoint: str) -> None:
    with pytest.raises(ValueError):
        AzureReceiptRecognizer(endpoint=endpoint, key=SecretStr(KEY))
