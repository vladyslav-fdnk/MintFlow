"""The provider-independent recognition boundary (receipt_recognition_design.md, R1, R6).

A ``ReceiptRecognizer`` turns image bytes into candidates. ``select_values``
applies MintFlow's own rules to keep only values safe to prefill: an incorrect
confident value is worse than a missing one (MVP section 7), so every rule
drops a field rather than guess. No provider type crosses this module.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from mintflow.application.capture.transaction_dates import is_within_future_tolerance
from mintflow.domain.capture import CurrencyCode, MerchantName, Money, TransactionDate
from mintflow.domain.user import Timezone

MERCHANT_MIN_CONFIDENCE: Final = 0.6
DATE_MIN_CONFIDENCE: Final = 0.7
TOTAL_MIN_CONFIDENCE: Final = 0.8
CURRENCY_MIN_CONFIDENCE: Final = 0.8
# The best value must lead any different value by this much to be trusted.
MIN_LEAD_OVER_ALTERNATIVE: Final = 0.15
# A receipt date older than this is implausible for a new capture.
MAX_RECEIPT_AGE: Final = timedelta(days=366)


class RecognitionUnavailable(Exception):
    """The recognizer could not produce any output (outage, timeout, unreadable image)."""


class AmountLabel(StrEnum):
    TOTAL = "total"
    SUBTOTAL = "subtotal"
    UNLABELED = "unlabeled"


class CurrencyEvidence(StrEnum):
    EXPLICIT_CODE = "explicit_code"  # "EUR", "PLN" printed on the receipt
    UNAMBIGUOUS_SYMBOL = "unambiguous_symbol"  # "€", "£", "₴"
    AMBIGUOUS_SYMBOL = "ambiguous_symbol"  # "$", "¥", "kr"


_TRUSTED_CURRENCY_EVIDENCE: Final = frozenset(
    {CurrencyEvidence.EXPLICIT_CODE, CurrencyEvidence.UNAMBIGUOUS_SYMBOL}
)


@dataclass(frozen=True, slots=True)
class MerchantCandidate:
    text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class DateCandidate:
    """Every calendar reading of one printed date; ``03/04`` has two."""

    readings: tuple[date, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class TotalCandidate:
    value: Decimal
    label: AmountLabel
    confidence: float


@dataclass(frozen=True, slots=True)
class CurrencyCandidate:
    """``code`` is the ISO 4217 code the evidence points to; an adapter maps "€" to "EUR"."""

    code: str
    evidence: CurrencyEvidence
    confidence: float


@dataclass(frozen=True, slots=True)
class RecognitionOutput:
    merchants: tuple[MerchantCandidate, ...] = ()
    dates: tuple[DateCandidate, ...] = ()
    totals: tuple[TotalCandidate, ...] = ()
    currencies: tuple[CurrencyCandidate, ...] = ()


class ReceiptRecognizer(Protocol):
    def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
        """Raises RecognitionUnavailable when no output can be produced."""
        ...


@dataclass(frozen=True, slots=True)
class SelectedValues:
    merchant: MerchantName | None
    transaction_date: TransactionDate | None
    total: Money | None


def _leader[T](scored: Sequence[tuple[T, float]], *, minimum: float) -> T | None:
    """The best value, if it clears ``minimum`` and leads every *different* value."""
    if not scored:
        return None
    best: dict[T, float] = {}
    for value, confidence in scored:
        best[value] = max(confidence, best.get(value, 0.0))
    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    value, confidence = ranked[0]
    if confidence < minimum:
        return None
    if len(ranked) > 1 and confidence - ranked[1][1] < MIN_LEAD_OVER_ALTERNATIVE:
        return None
    return value


def _select_merchant(candidates: Sequence[MerchantCandidate]) -> MerchantName | None:
    names: list[tuple[str, float]] = []
    for candidate in candidates:
        try:
            names.append((MerchantName(candidate.text).value, candidate.confidence))
        except ValueError:
            continue
    chosen = _leader(names, minimum=MERCHANT_MIN_CONFIDENCE)
    return MerchantName(chosen) if chosen is not None else None


def _select_date(
    candidates: Sequence[DateCandidate], *, now: datetime, timezone: Timezone
) -> TransactionDate | None:
    today = now.astimezone(ZoneInfo(timezone.value)).date()
    scored: list[tuple[date, float]] = []
    for candidate in candidates:
        plausible = {
            reading
            for reading in candidate.readings
            if reading >= today - MAX_RECEIPT_AGE
            and _is_valid_transaction_date(reading)
            and is_within_future_tolerance(TransactionDate(reading), now=now, timezone=timezone)
        }
        # Only a single plausible reading is trusted; otherwise the user chooses.
        if len(plausible) == 1:
            scored.append((plausible.pop(), candidate.confidence))
    chosen = _leader(scored, minimum=DATE_MIN_CONFIDENCE)
    return TransactionDate(chosen) if chosen is not None else None


def _is_valid_transaction_date(reading: date) -> bool:
    try:
        TransactionDate(reading)
    except ValueError:
        return False
    return True


def _select_currency(candidates: Sequence[CurrencyCandidate]) -> CurrencyCode | None:
    scored: list[tuple[str, float]] = []
    for candidate in candidates:
        if candidate.evidence not in _TRUSTED_CURRENCY_EVIDENCE:
            continue
        try:
            scored.append((CurrencyCode(candidate.code).value, candidate.confidence))
        except ValueError:
            continue
    chosen = _leader(scored, minimum=CURRENCY_MIN_CONFIDENCE)
    return CurrencyCode(chosen) if chosen is not None else None


def _select_total(
    candidates: Sequence[TotalCandidate], currency: CurrencyCode | None
) -> Money | None:
    if currency is None:
        # A total is only a financial fact together with its currency; never guess one.
        return None
    scored = [
        (candidate.value, candidate.confidence)
        for candidate in candidates
        if candidate.label is AmountLabel.TOTAL and candidate.value > 0
    ]
    chosen = _leader(scored, minimum=TOTAL_MIN_CONFIDENCE)
    if chosen is None:
        return None
    scaled = chosen.scaleb(currency.minor_unit_exponent)
    if scaled != scaled.to_integral_value():
        return None
    try:
        return Money(minor_units=int(scaled), currency=currency)
    except ValueError:
        return None


def select_values(
    output: RecognitionOutput, *, now: datetime, timezone: Timezone
) -> SelectedValues:
    """Keep only values safe to prefill; the user's own settings never fill a gap."""
    return SelectedValues(
        merchant=_select_merchant(output.merchants),
        transaction_date=_select_date(output.dates, now=now, timezone=timezone),
        total=_select_total(output.totals, _select_currency(output.currencies)),
    )
