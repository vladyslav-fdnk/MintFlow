from datetime import date, datetime

import pytest

from mintflow.domain.capture import MerchantName, TransactionDate


def test_transaction_date_wraps_a_calendar_date() -> None:
    transaction_date = TransactionDate(date(2026, 8, 1))

    assert transaction_date.value == date(2026, 8, 1)
    assert not isinstance(transaction_date.value, datetime)


def test_transaction_date_rejects_absurd_years() -> None:
    with pytest.raises(ValueError, match="year must be between"):
        TransactionDate(date(1500, 1, 1))
    with pytest.raises(ValueError, match="year must be between"):
        TransactionDate(date(3000, 1, 1))


def test_merchant_name_normalizes_whitespace() -> None:
    assert MerchantName("  Coffee   Shop  ").value == "Coffee Shop"


def test_merchant_name_rejects_empty_or_whitespace_only() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        MerchantName("")
    with pytest.raises(ValueError, match="must not be empty"):
        MerchantName("   ")


def test_merchant_name_enforces_bounded_length() -> None:
    MerchantName("x" * MerchantName.MAX_LENGTH)

    with pytest.raises(ValueError, match="at most"):
        MerchantName("x" * (MerchantName.MAX_LENGTH + 1))
