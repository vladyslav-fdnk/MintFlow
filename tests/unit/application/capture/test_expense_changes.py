import json
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from mintflow.application.capture import (
    ExpenseChangeRecord,
    ExpenseChangeType,
    ExpenseField,
    ExpenseFieldChange,
    expense_field_values,
)
from mintflow.domain.capture import (
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _expense(*, merchant: MerchantName | None = None, note: str | None = None) -> Expense:
    return Expense.create(
        owner_id=uuid4(),
        money=Money(minor_units=1500, currency=CurrencyCode("USD")),
        transaction_date=TransactionDate(date(2026, 8, 4)),
        category_key="groceries",
        capture_draft_id=uuid4(),
        merchant=merchant,
        note=note,
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )


def _record(
    change_type: ExpenseChangeType, changes: tuple[ExpenseFieldChange, ...] = ()
) -> ExpenseChangeRecord:
    return ExpenseChangeRecord(
        expense_id=uuid4(),
        actor_user_id=uuid4(),
        occurred_at=NOW,
        change_type=change_type,
        changes=changes,
    )


def test_field_values_serialize_every_editable_field_to_json_safe_primitives() -> None:
    values = expense_field_values(_expense(merchant=MerchantName("Corner Shop"), note="milk"))

    assert values == {
        ExpenseField.AMOUNT_MINOR_UNITS: 1500,
        ExpenseField.CURRENCY: "USD",
        ExpenseField.TRANSACTION_DATE: "2026-08-04",
        ExpenseField.MERCHANT: "Corner Shop",
        ExpenseField.CATEGORY_KEY: "groceries",
        ExpenseField.NOTE: "milk",
    }
    assert json.loads(json.dumps(values)) == {key.value: value for key, value in values.items()}


def test_field_values_keep_absent_optional_fields_as_null() -> None:
    values = expense_field_values(_expense())

    assert values[ExpenseField.MERCHANT] is None
    assert values[ExpenseField.NOTE] is None


def test_edited_record_serializes_only_its_changes() -> None:
    record = _record(
        ExpenseChangeType.EDITED,
        (
            ExpenseFieldChange(ExpenseField.AMOUNT_MINOR_UNITS, 1500, 1750),
            ExpenseFieldChange(ExpenseField.MERCHANT, "Corner Shop", None),
            ExpenseFieldChange(ExpenseField.TRANSACTION_DATE, "2026-08-04", "2026-08-03"),
        ),
    )

    assert record.changes_document() == {
        "amount_minor_units": {"old": 1500, "new": 1750},
        "merchant": {"old": "Corner Shop", "new": None},
        "transaction_date": {"old": "2026-08-04", "new": "2026-08-03"},
    }


@pytest.mark.parametrize("change_type", [ExpenseChangeType.DELETED, ExpenseChangeType.RESTORED])
def test_deletion_state_records_have_no_changes_document(change_type: ExpenseChangeType) -> None:
    assert _record(change_type).changes_document() is None


def test_edited_record_requires_changes() -> None:
    with pytest.raises(ValueError, match="must list"):
        _record(ExpenseChangeType.EDITED)


@pytest.mark.parametrize("change_type", [ExpenseChangeType.DELETED, ExpenseChangeType.RESTORED])
def test_deletion_state_records_reject_changes(change_type: ExpenseChangeType) -> None:
    with pytest.raises(ValueError, match="carries no field changes"):
        _record(change_type, (ExpenseFieldChange(ExpenseField.NOTE, None, "x"),))


def test_edited_record_rejects_the_same_field_twice() -> None:
    with pytest.raises(ValueError, match="at most once"):
        _record(
            ExpenseChangeType.EDITED,
            (
                ExpenseFieldChange(ExpenseField.NOTE, None, "a"),
                ExpenseFieldChange(ExpenseField.NOTE, "a", "b"),
            ),
        )


def test_occurred_at_must_be_aware_and_is_normalized_to_utc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ExpenseChangeRecord(
            expense_id=uuid4(),
            actor_user_id=uuid4(),
            occurred_at=datetime(2026, 8, 5, 12, 0),
            change_type=ExpenseChangeType.DELETED,
        )
    record = ExpenseChangeRecord(
        expense_id=uuid4(),
        actor_user_id=uuid4(),
        occurred_at=NOW.astimezone(timezone(timedelta(hours=3))),
        change_type=ExpenseChangeType.DELETED,
    )

    assert record.occurred_at.tzinfo is UTC
    assert record.occurred_at == NOW


def test_field_change_must_change_the_value() -> None:
    with pytest.raises(ValueError, match="must change"):
        ExpenseFieldChange(ExpenseField.CATEGORY_KEY, "groceries", "groceries")


@pytest.mark.parametrize(
    ("expense_field", "old", "new"),
    [
        (ExpenseField.AMOUNT_MINOR_UNITS, 1500, None),
        (ExpenseField.CURRENCY, None, "EUR"),
        (ExpenseField.TRANSACTION_DATE, "2026-08-04", None),
        (ExpenseField.CATEGORY_KEY, None, "health"),
    ],
)
def test_required_fields_reject_null(
    expense_field: ExpenseField, old: str | int | None, new: str | int | None
) -> None:
    with pytest.raises(ValueError, match="cannot be null"):
        ExpenseFieldChange(expense_field, old, new)


@pytest.mark.parametrize(
    ("expense_field", "old", "new"),
    [
        (ExpenseField.AMOUNT_MINOR_UNITS, "1500", 1750),
        (ExpenseField.AMOUNT_MINOR_UNITS, True, 1750),
        (ExpenseField.CURRENCY, "USD", 978),
        (ExpenseField.MERCHANT, 7, "Shop"),
    ],
)
def test_values_must_have_the_fields_primitive_type(
    expense_field: ExpenseField, old: object, new: object
) -> None:
    with pytest.raises(ValueError, match="must be"):
        ExpenseFieldChange(expense_field, old, new)  # type: ignore[arg-type]


@pytest.mark.parametrize("expense_field", [ExpenseField.MERCHANT, ExpenseField.NOTE])
def test_optional_fields_may_be_set_or_cleared(expense_field: ExpenseField) -> None:
    ExpenseFieldChange(expense_field, None, "value")
    ExpenseFieldChange(expense_field, "value", None)
