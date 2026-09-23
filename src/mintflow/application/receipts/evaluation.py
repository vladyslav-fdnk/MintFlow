"""Scoring a recognizer against hand-checked receipts (receipt_recognition_design.md, R1).

Only the values ``select_values`` would prefill are scored, so the report shows
what users would actually see. Per field, a value is correct, missing (nothing
prefilled, the user types it), or confidently wrong (prefilled and wrong). The
candidate with the fewest confidently wrong values wins, then coverage.

Receipt contents never enter a report: it holds counts and file names only.
"""

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from mintflow.application.receipts.recognition import SelectedValues
from mintflow.domain.capture import MerchantName, Money, TransactionDate


class ScoredField(StrEnum):
    MERCHANT = "merchant"
    DATE = "date"
    TOTAL = "total"


class FieldOutcome(StrEnum):
    CORRECT = "correct"
    MISSING = "missing"
    WRONG = "wrong"
    # The receipt has no such value and nothing was prefilled.
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ExpectedValues:
    """What a person read on the receipt; None when the receipt does not show it."""

    merchant: MerchantName | None
    transaction_date: TransactionDate | None
    total: Money | None


def _same_merchant(selected: MerchantName, expected: MerchantName) -> bool:
    # Recognizers differ in letter case; any other difference counts as wrong.
    return selected.value.casefold() == expected.value.casefold()


def _outcome[T](selected: T | None, expected: T | None, *, same: bool) -> FieldOutcome:
    if selected is None:
        return FieldOutcome.MISSING if expected is not None else FieldOutcome.NOT_APPLICABLE
    return FieldOutcome.CORRECT if expected is not None and same else FieldOutcome.WRONG


def score(selected: SelectedValues, expected: ExpectedValues) -> dict[ScoredField, FieldOutcome]:
    merchant_same = (
        selected.merchant is not None
        and expected.merchant is not None
        and _same_merchant(selected.merchant, expected.merchant)
    )
    return {
        ScoredField.MERCHANT: _outcome(selected.merchant, expected.merchant, same=merchant_same),
        ScoredField.DATE: _outcome(
            selected.transaction_date,
            expected.transaction_date,
            same=selected.transaction_date == expected.transaction_date,
        ),
        ScoredField.TOTAL: _outcome(
            selected.total, expected.total, same=selected.total == expected.total
        ),
    }


@dataclass(slots=True)
class EvaluationReport:
    counts: dict[ScoredField, Counter[FieldOutcome]] = field(
        default_factory=lambda: {scored: Counter() for scored in ScoredField}
    )
    samples: int = 0
    # Samples the recognizer could not process at all (error, timeout, unreadable file).
    failures: int = 0
    durations_ms: list[int] = field(default_factory=list)

    def add(self, outcomes: dict[ScoredField, FieldOutcome], *, duration_ms: int) -> None:
        self.samples += 1
        self.durations_ms.append(duration_ms)
        for scored, outcome in outcomes.items():
            self.counts[scored][outcome] += 1

    def add_failure(self, expected: ExpectedValues, *, duration_ms: int) -> None:
        """A failure prefills nothing: every value the receipt shows is missing."""
        self.failures += 1
        self.add(score(SelectedValues(None, None, None), expected), duration_ms=duration_ms)

    @property
    def confidently_wrong(self) -> int:
        return sum(counter[FieldOutcome.WRONG] for counter in self.counts.values())

    def lines(self) -> list[str]:
        header = f"{'field':<10}{'correct':>9}{'missing':>9}{'wrong':>7}{'n/a':>6}"
        rows = [
            f"{scored.value:<10}"
            f"{counter[FieldOutcome.CORRECT]:>9}"
            f"{counter[FieldOutcome.MISSING]:>9}"
            f"{counter[FieldOutcome.WRONG]:>7}"
            f"{counter[FieldOutcome.NOT_APPLICABLE]:>6}"
            for scored, counter in self.counts.items()
        ]
        durations = sorted(self.durations_ms)
        median = durations[len(durations) // 2] if durations else 0
        slowest = durations[-1] if durations else 0
        return [
            f"samples: {self.samples}, recognizer failures: {self.failures}",
            header,
            *rows,
            f"confidently wrong in total: {self.confidently_wrong}",
            f"duration ms: median {median}, max {slowest}",
        ]
