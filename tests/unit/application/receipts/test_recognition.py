from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from mintflow.application.receipts import (
    AmountLabel,
    CurrencyCandidate,
    CurrencyEvidence,
    DateCandidate,
    MerchantCandidate,
    RecognitionOutput,
    RecognitionUnavailable,
    TotalCandidate,
    select_values,
)
from mintflow.domain.capture import CurrencyCode, MerchantName, Money, TransactionDate
from mintflow.domain.user import Timezone
from mintflow.infrastructure.recognition.fake import FakeReceiptRecognizer

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
UTC_ZONE = Timezone("UTC")
EUR_CODE = CurrencyCandidate("EUR", CurrencyEvidence.EXPLICIT_CODE, 0.95)


def _select(**fields: object) -> tuple[object, object, object]:
    values = select_values(RecognitionOutput(**fields), now=NOW, timezone=UTC_ZONE)  # type: ignore[arg-type]
    return values.merchant, values.transaction_date, values.total


def _total(
    value: str, confidence: float = 0.95, label: AmountLabel = AmountLabel.TOTAL
) -> TotalCandidate:
    return TotalCandidate(Decimal(value), label, confidence)


# --- totals ----------------------------------------------------------------------------------


def test_a_clear_total_with_an_explicit_currency_is_selected() -> None:
    _, _, total = _select(totals=(_total("12.50"),), currencies=(EUR_CODE,))

    assert total == Money(minor_units=1250, currency=CurrencyCode("EUR"))


@pytest.mark.parametrize(
    ("totals", "reason"),
    [
        pytest.param((_total("12.50"), _total("15.00", 0.9)), "two totals, similar confidence"),
        pytest.param((_total("12.50", label=AmountLabel.SUBTOTAL),), "subtotal only"),
        pytest.param((_total("12.50", label=AmountLabel.UNLABELED),), "unlabeled amount"),
        pytest.param((_total("12.50", 0.6),), "low confidence"),
        pytest.param((_total("12.505"),), "more decimals than EUR allows"),
        pytest.param((_total("0"),), "zero"),
        pytest.param((_total("-3.00"),), "negative"),
        pytest.param((_total("1000000000"),), "above the Money maximum"),
        pytest.param((), "no candidates"),
    ],
)
def test_uncertain_totals_are_dropped(totals: tuple[TotalCandidate, ...], reason: str) -> None:
    _, _, total = _select(totals=totals, currencies=(EUR_CODE,))

    assert total is None, reason


def test_a_clear_leader_among_different_totals_is_selected() -> None:
    _, _, total = _select(
        totals=(_total("12.50", 0.95), _total("15.00", 0.7)), currencies=(EUR_CODE,)
    )

    assert total == Money(minor_units=1250, currency=CurrencyCode("EUR"))


def test_the_same_total_read_twice_is_not_a_conflict() -> None:
    _, _, total = _select(totals=(_total("12.50"), _total("12.5", 0.9)), currencies=(EUR_CODE,))

    assert total == Money(minor_units=1250, currency=CurrencyCode("EUR"))


def test_a_subtotal_does_not_compete_with_the_total() -> None:
    _, _, total = _select(
        totals=(_total("12.50"), _total("10.00", 0.95, AmountLabel.SUBTOTAL)),
        currencies=(EUR_CODE,),
    )

    assert total == Money(minor_units=1250, currency=CurrencyCode("EUR"))


# --- currency --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("currencies", "expected"),
    [
        pytest.param(
            (CurrencyCandidate("€", CurrencyEvidence.UNAMBIGUOUS_SYMBOL, 0.9),),
            None,
            id="symbol text not a code",
        ),
        pytest.param(
            (CurrencyCandidate("EUR", CurrencyEvidence.UNAMBIGUOUS_SYMBOL, 0.9),),
            "EUR",
            id="unambiguous symbol",
        ),
        pytest.param(
            (CurrencyCandidate("USD", CurrencyEvidence.AMBIGUOUS_SYMBOL, 0.99),),
            None,
            id="dollar sign",
        ),
        pytest.param(
            (CurrencyCandidate("JPY", CurrencyEvidence.AMBIGUOUS_SYMBOL, 0.99),),
            None,
            id="yen sign",
        ),
        pytest.param(
            (CurrencyCandidate("ZZZ", CurrencyEvidence.EXPLICIT_CODE, 0.99),),
            None,
            id="unsupported code",
        ),
        pytest.param(
            (CurrencyCandidate("pln", CurrencyEvidence.EXPLICIT_CODE, 0.9),),
            "PLN",
            id="lower-case code",
        ),
        pytest.param(
            (
                CurrencyCandidate("EUR", CurrencyEvidence.EXPLICIT_CODE, 0.9),
                CurrencyCandidate("PLN", CurrencyEvidence.EXPLICIT_CODE, 0.85),
            ),
            None,
            id="two currencies",
        ),
    ],
)
def test_currency_needs_explicit_unambiguous_evidence(
    currencies: tuple[CurrencyCandidate, ...], expected: str | None
) -> None:
    _, _, total = _select(totals=(_total("12.50"),), currencies=currencies)

    if expected is None:
        assert total is None
    else:
        assert total == Money(minor_units=1250, currency=CurrencyCode(expected))


def test_a_total_without_a_currency_is_never_completed_from_user_settings() -> None:
    _, _, total = _select(totals=(_total("12.50"),))

    assert total is None


def test_currency_precision_applies_to_the_total() -> None:
    jpy = CurrencyCandidate("JPY", CurrencyEvidence.EXPLICIT_CODE, 0.95)

    assert _select(totals=(_total("1500"),), currencies=(jpy,))[2] == Money(
        minor_units=1500, currency=CurrencyCode("JPY")
    )
    assert _select(totals=(_total("1500.50"),), currencies=(jpy,))[2] is None


# --- dates -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("readings", "expected"),
    [
        pytest.param((date(2026, 9, 20),), date(2026, 9, 20), id="single reading"),
        pytest.param((date(2026, 4, 3), date(2026, 3, 4)), None, id="03/04 both plausible"),
        pytest.param(
            (date(2026, 9, 13), date(2027, 1, 9)),
            date(2026, 9, 13),
            id="other reading in the future",
        ),
        pytest.param((date(2026, 9, 25),), None, id="beyond future tolerance"),
        pytest.param((date(2026, 9, 24),), date(2026, 9, 24), id="tomorrow is tolerated"),
        pytest.param((date(2025, 9, 1),), None, id="older than a year"),
        pytest.param((date(1999, 1, 1),), None, id="outside supported years"),
    ],
)
def test_dates_are_selected_only_when_one_reading_is_plausible(
    readings: tuple[date, ...], expected: date | None
) -> None:
    _, transaction_date, _ = _select(dates=(DateCandidate(readings, 0.9),))

    assert transaction_date == (TransactionDate(expected) if expected else None)


def test_date_plausibility_uses_the_users_timezone() -> None:
    # 23:30 UTC on 23 Sep is 24 Sep in Tokyo, so 25 Sep is only "tomorrow" there.
    late = datetime(2026, 9, 23, 23, 30, tzinfo=UTC)
    candidate = RecognitionOutput(dates=(DateCandidate((date(2026, 9, 25),), 0.9),))

    assert select_values(candidate, now=late, timezone=Timezone("Asia/Tokyo")).transaction_date
    assert select_values(candidate, now=late, timezone=UTC_ZONE).transaction_date is None


def test_conflicting_dates_are_dropped() -> None:
    _, transaction_date, _ = _select(
        dates=(DateCandidate((date(2026, 9, 20),), 0.9), DateCandidate((date(2026, 9, 21),), 0.85))
    )

    assert transaction_date is None


# --- merchant --------------------------------------------------------------------------------


def test_the_merchant_is_normalized_and_needs_confidence() -> None:
    assert _select(merchants=(MerchantCandidate("  Corner   Shop ", 0.8),))[0] == MerchantName(
        "Corner Shop"
    )
    assert _select(merchants=(MerchantCandidate("Corner Shop", 0.5),))[0] is None
    assert _select(merchants=(MerchantCandidate("   ", 0.99),))[0] is None
    assert _select(merchants=(MerchantCandidate("x" * 141, 0.99),))[0] is None


# --- a partial, realistic output --------------------------------------------------------------


def test_missing_fields_stay_missing() -> None:
    merchant, transaction_date, total = _select(merchants=(MerchantCandidate("Kiosk", 0.9),))

    assert merchant == MerchantName("Kiosk")
    assert (transaction_date, total) == (None, None)


# --- the fake recognizer ----------------------------------------------------------------------


def test_the_fake_recognizer_is_deterministic_and_can_fail() -> None:
    known = RecognitionOutput(merchants=(MerchantCandidate("Kiosk", 0.9),))
    fake = FakeReceiptRecognizer(outputs={b"known": known}, failing={b"broken"})

    assert fake.recognize(image=b"known", media_type="image/jpeg") == known
    assert fake.recognize(image=b"other", media_type="image/png") == RecognitionOutput()
    with pytest.raises(RecognitionUnavailable):
        fake.recognize(image=b"broken", media_type="image/jpeg")
    assert fake.calls == [b"known", b"other", b"broken"]
