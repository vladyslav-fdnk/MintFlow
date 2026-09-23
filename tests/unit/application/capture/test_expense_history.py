import base64
import json
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from mintflow.application.capture import (
    ExpenseHistoryFilter,
    ExpenseHistoryPosition,
    InvalidExpenseHistoryCursor,
    decode_history_cursor,
    encode_history_cursor,
)
from mintflow.domain.capture import CurrencyCode

POSITION = ExpenseHistoryPosition(
    transaction_date=date(2026, 8, 4),
    created_at=datetime(2026, 8, 5, 12, 0, 0, 123456, tzinfo=UTC),
    expense_id=UUID("0f8fad5b-d9cb-469f-a165-70867728950e"),
)


def _encode_payload(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_cursor_round_trips_exactly() -> None:
    cursor = encode_history_cursor(POSITION)

    assert decode_history_cursor(cursor) == POSITION


def test_cursor_is_url_safe_and_unpadded() -> None:
    cursor = encode_history_cursor(POSITION)

    assert "=" not in cursor
    assert set(cursor) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_cursor_normalizes_created_at_offset_to_the_same_instant() -> None:
    shifted = ExpenseHistoryPosition(
        transaction_date=POSITION.transaction_date,
        created_at=POSITION.created_at.astimezone(timezone(timedelta(hours=3))),
        expense_id=POSITION.expense_id,
    )

    decoded = decode_history_cursor(encode_history_cursor(shifted))

    assert decoded.created_at == POSITION.created_at


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("", id="empty"),
        pytest.param("a" * 257, id="too long"),
        pytest.param("not base64!", id="not base64"),
        pytest.param(encode_history_cursor(POSITION)[:-4], id="truncated"),
        pytest.param(encode_history_cursor(POSITION) + "=", id="padded non-canonical"),
        pytest.param(base64.urlsafe_b64encode(b"\xff\xfe").decode().rstrip("="), id="not utf-8"),
        pytest.param(_encode_payload([1, 2, 3]), id="not an object"),
        pytest.param(_encode_payload({"d": "2026-08-04", "c": "x"}), id="missing key"),
        pytest.param(
            _encode_payload(
                {"d": "2026-08-04", "c": POSITION.created_at.isoformat(), "i": "x", "z": 1}
            ),
            id="extra key",
        ),
        pytest.param(
            _encode_payload(
                {"d": 20260804, "c": POSITION.created_at.isoformat(), "i": str(POSITION.expense_id)}
            ),
            id="wrong type",
        ),
        pytest.param(
            _encode_payload(
                {
                    "d": "2026-13-01",
                    "c": POSITION.created_at.isoformat(),
                    "i": str(POSITION.expense_id),
                }
            ),
            id="invalid date",
        ),
        pytest.param(
            _encode_payload(
                {"d": "2026-08-04", "c": "2026-08-05T12:00:00", "i": str(POSITION.expense_id)}
            ),
            id="naive datetime",
        ),
        pytest.param(
            _encode_payload(
                {"d": "2026-08-04", "c": POSITION.created_at.isoformat(), "i": "not-a-uuid"}
            ),
            id="invalid uuid",
        ),
        pytest.param(
            _encode_payload(
                {
                    "d": "2026-08-04",
                    "c": POSITION.created_at.isoformat(),
                    "i": str(POSITION.expense_id).upper(),
                }
            ),
            id="non-canonical uuid",
        ),
    ],
)
def test_malformed_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(InvalidExpenseHistoryCursor):
        decode_history_cursor(cursor)


def test_position_requires_an_aware_created_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ExpenseHistoryPosition(
            transaction_date=date(2026, 8, 4),
            created_at=datetime(2026, 8, 5, 12, 0),
            expense_id=POSITION.expense_id,
        )


def test_filter_rejects_an_inverted_date_range() -> None:
    with pytest.raises(ValueError, match="date_from"):
        ExpenseHistoryFilter(date_from=date(2026, 8, 5), date_to=date(2026, 8, 4))


def test_filter_accepts_a_single_day_range_and_defaults_to_no_filtering() -> None:
    single_day = ExpenseHistoryFilter(date_from=date(2026, 8, 4), date_to=date(2026, 8, 4))
    default = ExpenseHistoryFilter()

    assert single_day.date_from == single_day.date_to
    assert default.category_keys == frozenset()
    assert default.currencies == frozenset()


def test_filter_holds_currency_codes() -> None:
    history_filter = ExpenseHistoryFilter(currencies=frozenset({CurrencyCode("EUR")}))

    assert CurrencyCode("EUR") in history_filter.currencies
