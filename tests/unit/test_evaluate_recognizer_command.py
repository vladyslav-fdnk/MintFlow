import json
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from pydantic import SecretStr

from mintflow.application.receipts import (
    AmountLabel,
    CurrencyCandidate,
    CurrencyEvidence,
    DateCandidate,
    ExpectedValues,
    FieldOutcome,
    MerchantCandidate,
    RecognitionOutput,
    ScoredField,
    SelectedValues,
    TotalCandidate,
    score,
)
from mintflow.commands import evaluate_recognizer
from mintflow.commands.evaluate_recognizer import SampleError, evaluate, main
from mintflow.config import Settings
from mintflow.domain.capture import CurrencyCode, MerchantName, Money, TransactionDate
from mintflow.domain.user import Timezone
from mintflow.infrastructure.recognition.fake import FakeReceiptRecognizer

JPEG = b"\xff\xd8\xff\xe0"
EUR = CurrencyCode("EUR")
EXPECTED = ExpectedValues(
    merchant=MerchantName("Corner Shop"),
    transaction_date=TransactionDate(date(2026, 9, 20)),
    total=Money(minor_units=1250, currency=EUR),
)


@pytest.mark.parametrize(
    ("selected", "outcomes"),
    [
        pytest.param(
            SelectedValues(
                MerchantName("CORNER SHOP"),
                TransactionDate(date(2026, 9, 20)),
                Money(minor_units=1250, currency=EUR),
            ),
            ("correct", "correct", "correct"),
            id="all correct, merchant case ignored",
        ),
        pytest.param(SelectedValues(None, None, None), ("missing",) * 3, id="nothing prefilled"),
        pytest.param(
            SelectedValues(
                MerchantName("Corner"),
                TransactionDate(date(2026, 9, 21)),
                Money(minor_units=1250, currency=CurrencyCode("USD")),
            ),
            ("wrong", "wrong", "wrong"),
            id="currency is part of the total",
        ),
    ],
)
def test_each_field_is_correct_missing_or_confidently_wrong(
    selected: SelectedValues, outcomes: tuple[str, str, str]
) -> None:
    assert tuple(outcome.value for outcome in score(selected, EXPECTED).values()) == outcomes


def test_a_value_the_receipt_does_not_show_is_wrong_only_when_prefilled() -> None:
    absent = ExpectedValues(merchant=None, transaction_date=None, total=None)

    assert score(SelectedValues(None, None, None), absent)[ScoredField.MERCHANT] is (
        FieldOutcome.NOT_APPLICABLE
    )
    assert (
        score(SelectedValues(MerchantName("Kiosk"), None, None), absent)[ScoredField.MERCHANT]
        is FieldOutcome.WRONG
    )


def _samples(tmp_path: Path, files: dict[str, bytes], entries: list[dict[str, object]]) -> Path:
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / "expected.json").write_text(json.dumps({"samples": entries}))
    return tmp_path


ENTRY = {
    "merchant": "Corner Shop",
    "date": "2026-09-20",
    "total": "12.50",
    "currency": "EUR",
}
READ = RecognitionOutput(
    merchants=(MerchantCandidate("Corner Shop", 0.9),),
    dates=(DateCandidate((date(2026, 9, 20),), 0.9),),
    totals=(TotalCandidate(Decimal("12.50"), AmountLabel.TOTAL, 0.95),),
    currencies=(CurrencyCandidate("EUR", CurrencyEvidence.EXPLICIT_CODE, 0.95),),
)
WRONG_TOTAL = RecognitionOutput(
    totals=(TotalCandidate(Decimal("21.50"), AmountLabel.TOTAL, 0.95),),
    currencies=(CurrencyCandidate("EUR", CurrencyEvidence.EXPLICIT_CODE, 0.95),),
)


def test_the_report_counts_outcomes_without_receipt_contents(tmp_path: Path) -> None:
    samples = _samples(
        tmp_path,
        {"a.jpg": JPEG + b"a", "b.jpg": JPEG + b"b", "c.jpg": JPEG + b"c", "d.pdf": b"%PDF"},
        [{"file": name, **ENTRY} for name in ("a.jpg", "b.jpg", "c.jpg", "d.pdf")],
    )
    recognizer = FakeReceiptRecognizer(
        outputs={JPEG + b"a": READ, JPEG + b"b": WRONG_TOTAL}, failing={JPEG + b"c"}
    )
    out = StringIO()

    report = evaluate(recognizer, samples, timezone=Timezone("UTC"), out=out)

    assert (report.samples, report.failures, report.confidently_wrong) == (4, 2, 1)
    assert report.counts[ScoredField.TOTAL][FieldOutcome.CORRECT] == 1
    assert report.counts[ScoredField.TOTAL][FieldOutcome.WRONG] == 1
    assert report.counts[ScoredField.TOTAL][FieldOutcome.MISSING] == 2
    printed = out.getvalue() + "\n".join(report.lines())
    assert "a.jpg: merchant correct, date correct, total correct" in printed
    assert "c.jpg: recognizer failed (RecognitionUnavailable)" in printed
    assert "d.pdf: not an accepted image" in printed
    for content in ("Corner Shop", "12.50", "21.50", "2026-09-20"):
        assert content not in printed


def test_date_plausibility_uses_the_day_the_photo_was_taken(tmp_path: Path) -> None:
    # Two years later the date would be dropped as too old; on the day it is kept.
    samples = _samples(
        tmp_path,
        {"a.jpg": JPEG},
        [{"file": "a.jpg", **ENTRY, "photographed_on": "2028-09-20"}],
    )
    report = evaluate(
        FakeReceiptRecognizer(default=READ), samples, timezone=Timezone("UTC"), out=StringIO()
    )
    assert report.counts[ScoredField.DATE][FieldOutcome.MISSING] == 1

    samples = _samples(tmp_path, {"a.jpg": JPEG}, [{"file": "a.jpg", **ENTRY}])
    report = evaluate(
        FakeReceiptRecognizer(default=READ), samples, timezone=Timezone("UTC"), out=StringIO()
    )
    assert report.counts[ScoredField.DATE][FieldOutcome.CORRECT] == 1


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param([{"file": "../outside.jpg", **ENTRY}], id="path outside the folder"),
        pytest.param([{**ENTRY}], id="no file"),
        pytest.param([{"file": "a.jpg", **ENTRY, "total": "12.505"}], id="bad total"),
        pytest.param([{"file": "a.jpg", **ENTRY, "currency": "ZZZ"}], id="bad currency"),
    ],
)
def test_unusable_manifests_are_refused(tmp_path: Path, entries: list[dict[str, object]]) -> None:
    samples = _samples(tmp_path, {"a.jpg": JPEG}, entries)

    with pytest.raises(SampleError):
        evaluate(FakeReceiptRecognizer(), samples, timezone=Timezone("UTC"), out=StringIO())


def test_main_uses_the_configured_recognizer_and_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    samples = _samples(tmp_path, {"a.jpg": JPEG}, [{"file": "a.jpg", **ENTRY}])
    monkeypatch.setattr(evaluate_recognizer, "get_settings", lambda: _settings())
    assert main(["--samples", str(samples)]) == 2
    assert "no receipt recognizer is configured" in capsys.readouterr().err

    monkeypatch.setattr(
        evaluate_recognizer, "get_settings", lambda: _settings(receipt_recognizer="fake")
    )
    assert main(["--samples", str(samples)]) == 0
    assert "samples: 1, recognizer failures: 0" in capsys.readouterr().out

    assert main(["--samples", str(tmp_path / "missing")]) == 2
    assert "evaluation failed" in capsys.readouterr().err


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_url=SecretStr("postgresql://localhost/database"),
        authentication_rate_limit_key=SecretStr("rate"),
        authentication_csrf_signing_key=SecretStr("csrf"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
        **overrides,  # type: ignore[arg-type]
    )
