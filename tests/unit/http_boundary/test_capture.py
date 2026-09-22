from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import ValidationError

from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.http.capture import (
    MAX_CAPTURE_REQUEST_BODY_BYTES,
    EditCaptureDraftRequest,
    _malformed,
    _not_found,
    _rejected,
    _to_response,
    parse_edit_capture_draft_request,
)

OWNER = uuid4()
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _request_with_body(body: bytes) -> Request:
    scope = {
        "type": "http",
        "method": "PATCH",
        "path": "/capture/drafts/00000000-0000-0000-0000-000000000000",
        "headers": [],
        "client": ("192.0.2.10", 1234),
        "server": ("test", 80),
        "scheme": "http",
        "query_string": b"",
        "root_path": "",
        "http_version": "1.1",
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_edit_request_accepts_amount_and_currency_together() -> None:
    request = EditCaptureDraftRequest(amount_minor_units=1000, currency="USD")

    assert request.amount_minor_units == 1000
    assert request.currency == "USD"


def test_edit_request_rejects_amount_without_currency() -> None:
    with pytest.raises(ValidationError, match="must be provided together"):
        EditCaptureDraftRequest(amount_minor_units=1000)


def test_edit_request_rejects_currency_without_amount() -> None:
    with pytest.raises(ValidationError, match="must be provided together"):
        EditCaptureDraftRequest(currency="USD")


def test_edit_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EditCaptureDraftRequest.model_validate({"unexpected": "field"})


def test_edit_request_rejects_a_note_over_the_length_bound() -> None:
    with pytest.raises(ValidationError):
        EditCaptureDraftRequest(note="x" * 2001)


def test_edit_request_rejects_an_empty_merchant() -> None:
    with pytest.raises(ValidationError):
        EditCaptureDraftRequest(merchant="")


def test_edit_request_all_fields_optional() -> None:
    request = EditCaptureDraftRequest()

    assert request.amount_minor_units is None
    assert request.note is None


@pytest.mark.anyio
async def test_parse_edit_request_accepts_a_well_formed_body() -> None:
    request = _request_with_body(b'{"note": "Team lunch"}')

    parsed = await parse_edit_capture_draft_request(request)

    assert parsed == EditCaptureDraftRequest(note="Team lunch")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"amount_minor_units": 100}',
        b'{"unexpected": true}',
        b"x" * (MAX_CAPTURE_REQUEST_BODY_BYTES + 1),
    ],
)
async def test_parse_edit_request_rejects_malformed_or_oversized_bodies(body: bytes) -> None:
    request = _request_with_body(body)

    assert await parse_edit_capture_draft_request(request) is None


def test_to_response_maps_every_field() -> None:
    draft = CaptureDraft.start(owner_id=OWNER, source=CaptureSource.WEB_MANUAL, now=NOW)
    draft = draft.set_amount(
        caller_id=OWNER, amount=Money(minor_units=1500, currency=CurrencyCode("USD")), now=NOW
    )
    draft = draft.set_transaction_date(
        caller_id=OWNER, transaction_date=TransactionDate(date(2026, 8, 6)), now=NOW
    )
    draft = draft.set_merchant(caller_id=OWNER, merchant=MerchantName("Coffee Shop"), now=NOW)

    response = _to_response(draft)

    assert response.id == draft.id
    assert response.state == "collecting"
    assert response.amount_minor_units == 1500
    assert response.currency == "USD"
    assert response.amount_source == "user"
    assert response.transaction_date == date(2026, 8, 6)
    assert response.merchant == "Coffee Shop"
    assert response.category_key is None
    assert response.expense_id is None


def test_to_response_handles_an_empty_draft() -> None:
    draft = CaptureDraft.start(owner_id=OWNER, source=CaptureSource.WEB_MANUAL, now=NOW)

    response = _to_response(draft)

    assert response.amount_minor_units is None
    assert response.currency is None
    assert response.merchant is None
    assert response.note is None


def test_error_factories_are_generic_and_carry_security_headers() -> None:
    for factory, status_code in ((_not_found, 404), (_rejected, 409), (_malformed, 422)):
        error = factory()
        assert error.status_code == status_code
        assert error.headers is not None
        assert error.headers["Cache-Control"] == "no-store"
        assert error.headers["Referrer-Policy"] == "no-referrer"
