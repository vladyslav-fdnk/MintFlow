from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import ValidationError

from mintflow.application.capture import (
    UNCHANGED,
    ExpenseEdit,
    ExpenseHistoryFilter,
    ExpenseHistoryPosition,
    encode_history_cursor,
)
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.http.capture import (
    DEFAULT_HISTORY_PAGE_SIZE,
    MAX_CAPTURE_REQUEST_BODY_BYTES,
    EditCaptureDraftRequest,
    EditExpenseRequest,
    _expense_not_found,
    _malformed,
    _not_found,
    _rejected,
    _to_expense_response,
    _to_response,
    parse_edit_capture_draft_request,
    parse_edit_expense_request,
    parse_expense_history_query,
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
    for factory, status_code in (
        (_not_found, 404),
        (_rejected, 409),
        (_malformed, 422),
        (_expense_not_found, 404),
    ):
        error = factory()
        assert error.status_code == status_code
        assert error.headers is not None
        assert error.headers["Cache-Control"] == "no-store"
        assert error.headers["Referrer-Policy"] == "no-referrer"


def test_expense_not_found_and_draft_not_found_are_distinct_messages() -> None:
    assert _not_found().detail != _expense_not_found().detail


def test_to_expense_response_maps_every_field() -> None:
    expense = Expense.create(
        owner_id=OWNER,
        money=Money(minor_units=2500, currency=CurrencyCode("EUR")),
        transaction_date=TransactionDate(date(2026, 8, 6)),
        category_key="groceries",
        capture_draft_id=uuid4(),
        merchant=MerchantName("Coffee Shop"),
        note="Team lunch",
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )

    response = _to_expense_response(expense)

    assert response.id == expense.id
    assert response.amount_minor_units == 2500
    assert response.currency == "EUR"
    assert response.transaction_date == date(2026, 8, 6)
    assert response.merchant == "Coffee Shop"
    assert response.category_key == "groceries"
    assert response.note == "Team lunch"
    assert response.source == "web_manual"
    assert response.capture_draft_id == expense.capture_draft_id


def test_to_expense_response_handles_no_merchant() -> None:
    expense = Expense.create(
        owner_id=OWNER,
        money=Money(minor_units=1000, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 6)),
        category_key="uncategorized",
        capture_draft_id=uuid4(),
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )

    response = _to_expense_response(expense)

    assert response.merchant is None
    assert response.note is None


def test_expense_edit_request_with_no_fields_changes_nothing() -> None:
    assert EditExpenseRequest.model_validate_json(b"{}").to_edit() == ExpenseEdit()


def test_expense_edit_request_maps_every_supplied_field_to_domain_values() -> None:
    request = EditExpenseRequest.model_validate_json(
        b'{"amount_minor_units": 1750, "currency": "EUR", "transaction_date": "2026-08-03",'
        b' "merchant": "  Corner   Shop ", "category_key": "health", "note": "bread"}'
    )

    assert request.to_edit() == ExpenseEdit(
        money=Money(minor_units=1750, currency=CurrencyCode("EUR")),
        transaction_date=TransactionDate(date(2026, 8, 3)),
        merchant=MerchantName("Corner Shop"),
        category_key="health",
        note="bread",
    )


def test_expense_edit_request_distinguishes_absent_from_null() -> None:
    cleared = EditExpenseRequest.model_validate_json(b'{"merchant": null, "note": null}')
    absent = EditExpenseRequest.model_validate_json(b'{"note": "kept"}')

    assert cleared.to_edit() == ExpenseEdit(merchant=None, note=None)
    assert absent.to_edit().merchant is UNCHANGED
    assert absent.to_edit().note == "kept"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"amount_minor_units": null, "currency": null}', id="null money"),
        pytest.param(b'{"transaction_date": null}', id="null date"),
        pytest.param(b'{"category_key": null}', id="null category"),
        pytest.param(b'{"amount_minor_units": 100}', id="amount without currency"),
        pytest.param(b'{"currency": "USD"}', id="currency without amount"),
        pytest.param(b'{"amount_minor_units": -1, "currency": "USD"}', id="negative amount"),
        pytest.param(b'{"merchant": ""}', id="empty merchant"),
        pytest.param(b'{"note": "' + b"x" * 2001 + b'"}', id="note too long"),
        pytest.param(b'{"source": "web_manual"}', id="unknown field"),
        pytest.param(b'{"deleted_at": null}', id="unknown nullable field"),
        pytest.param(b"[]", id="not an object"),
    ],
)
def test_expense_edit_request_rejects_malformed_bodies(body: bytes) -> None:
    with pytest.raises(ValidationError):
        EditExpenseRequest.model_validate_json(body)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"amount_minor_units": 100, "currency": "ZZZ"}', id="unsupported currency"),
        pytest.param(b'{"merchant": "   "}', id="blank merchant"),
        pytest.param(b'{"transaction_date": "1999-12-31"}', id="date out of range"),
    ],
)
def test_expense_edit_request_invalid_domain_values_raise_value_error(body: bytes) -> None:
    request = EditExpenseRequest.model_validate_json(body)

    with pytest.raises(ValueError):
        request.to_edit()


@pytest.mark.anyio
async def test_parse_expense_edit_request_rejects_oversized_bodies() -> None:
    body = b'{"note": "' + b"x" * MAX_CAPTURE_REQUEST_BODY_BYTES + b'"}'

    assert await parse_edit_expense_request(_request_with_body(body)) is None
    assert await parse_edit_expense_request(_request_with_body(b"not json")) is None


def _request_with_query(query_string: bytes) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/capture/expenses",
            "headers": [],
            "query_string": query_string,
        }
    )


def test_history_query_defaults_to_no_filters_and_the_default_page_size() -> None:
    query = parse_expense_history_query(_request_with_query(b""))

    assert query is not None
    assert query.history_filter == ExpenseHistoryFilter()
    assert query.limit == DEFAULT_HISTORY_PAGE_SIZE
    assert query.after is None


def test_history_query_parses_every_parameter() -> None:
    position = ExpenseHistoryPosition(
        transaction_date=date(2026, 8, 4), created_at=NOW, expense_id=uuid4()
    )
    cursor = encode_history_cursor(position)
    query = parse_expense_history_query(
        _request_with_query(
            b"date_from=2026-08-01&date_to=2026-08-31&category=groceries&category=health"
            b"&currency=usd&currency=EUR&limit=100&cursor=" + cursor.encode()
        )
    )

    assert query is not None
    assert query.history_filter == ExpenseHistoryFilter(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        category_keys=frozenset({"groceries", "health"}),
        currencies=frozenset({CurrencyCode("USD"), CurrencyCode("EUR")}),
    )
    assert query.limit == 100
    assert query.after == position


@pytest.mark.parametrize(
    "query_string",
    [
        pytest.param(b"date_from=2026-8-1", id="non-iso date"),
        pytest.param(b"date_from=20260801", id="compact date"),
        pytest.param(b"date_to=2026-02-30", id="impossible date"),
        pytest.param(b"date_from=2026-08-05&date_to=2026-08-04", id="inverted range"),
        pytest.param(b"date_from=2026-08-01&date_from=2026-08-02", id="repeated date"),
        pytest.param(b"currency=ZZZ", id="unsupported currency"),
        pytest.param(b"category=", id="empty category"),
        pytest.param(b"category=" + b"x" * 33, id="overlong category"),
        pytest.param(b"&".join([b"category=c%d" % i for i in range(33)]), id="too many categories"),
        pytest.param(b"limit=0", id="zero limit"),
        pytest.param(b"limit=101", id="limit over maximum"),
        pytest.param(b"limit=-5", id="negative limit"),
        pytest.param(b"limit=05", id="leading zero limit"),
        pytest.param(b"limit=ten", id="non-numeric limit"),
        pytest.param("limit=١٠".encode(), id="non-ascii digits"),
        pytest.param(b"limit=5&limit=6", id="repeated limit"),
        pytest.param(b"cursor=not-a-cursor", id="malformed cursor"),
        pytest.param(b"cursor=", id="empty cursor"),
        pytest.param(b"merchant=Shop", id="unknown parameter"),
    ],
)
def test_history_query_rejects_invalid_parameters(query_string: bytes) -> None:
    assert parse_expense_history_query(_request_with_query(query_string)) is None
