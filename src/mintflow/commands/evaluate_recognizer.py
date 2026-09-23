"""Compare a receipt recognizer with hand-checked receipts (design R1, RCPT-07).

    python -m mintflow.commands.evaluate_recognizer --samples DIR

The recognizer is the one configured for the worker (MINTFLOW_RECEIPT_RECOGNIZER).

DIR holds consented receipt images and ``expected.json``, written by a person:

    {"samples": [{"file": "r01.jpg", "merchant": "Corner Shop", "date": "2026-09-20",
                  "total": "12.50", "currency": "EUR", "photographed_on": "2026-09-20"}]}

A value the receipt does not show is null. ``photographed_on`` is the day the
photo would have been sent (default: the receipt date), because date plausibility
depends on it. Keep the samples outside the repository.

The report prints counts and file names only, never receipt contents, so it can
be shared when choosing a provider. Runs manually; it may call a real provider.
"""

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from datetime import time as day_time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, TextIO
from zoneinfo import ZoneInfo

from mintflow.application.receipts import (
    MAX_RECEIPT_IMAGE_BYTES,
    EvaluationReport,
    ExpectedValues,
    ReceiptRecognizer,
    score,
    select_values,
    sniff_media_type,
)
from mintflow.commands.receipt_worker import build_recognizer
from mintflow.config import get_settings
from mintflow.domain.capture import CurrencyCode, MerchantName, Money, TransactionDate
from mintflow.domain.user import Timezone

MANIFEST: Final = "expected.json"


class SampleError(ValueError):
    """The manifest or a sample file is not usable."""


def _expected(entry: Mapping[str, object]) -> ExpectedValues:
    merchant, day, total, currency = (
        entry.get("merchant"),
        entry.get("date"),
        entry.get("total"),
        entry.get("currency"),
    )
    try:
        money = None
        if total is not None or currency is not None:
            code = CurrencyCode(str(currency))
            minor = Decimal(str(total)).scaleb(code.minor_unit_exponent)
            if minor != minor.to_integral_value():
                raise SampleError("total has more decimals than its currency allows")
            money = Money(minor_units=int(minor), currency=code)
        return ExpectedValues(
            merchant=MerchantName(str(merchant)) if merchant is not None else None,
            transaction_date=(
                TransactionDate(date.fromisoformat(str(day))) if day is not None else None
            ),
            total=money,
        )
    except (ValueError, InvalidOperation) as error:
        raise SampleError(f"invalid expected values: {error}") from None


def _photographed_at(entry: Mapping[str, object], zone: ZoneInfo) -> datetime:
    raw = entry.get("photographed_on") or entry.get("date")
    day = date.fromisoformat(str(raw)) if raw is not None else datetime.now(zone).date()
    return datetime.combine(day, day_time(12), tzinfo=zone).astimezone(UTC)


def evaluate(
    recognizer: ReceiptRecognizer, samples: Path, *, timezone: Timezone, out: TextIO
) -> EvaluationReport:
    try:
        manifest = json.loads((samples / MANIFEST).read_text(encoding="utf-8"))
        entries = manifest["samples"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SampleError(f"cannot read {MANIFEST}: {type(error).__name__}") from None
    zone = ZoneInfo(timezone.value)
    report = EvaluationReport()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise SampleError("every sample needs a file name")
        name = entry["file"]
        expected = _expected(entry)
        path = samples / name
        if path.resolve().parent != samples.resolve():
            raise SampleError(f"{name}: sample files must be directly inside the samples folder")
        content = path.read_bytes()
        media_type = sniff_media_type(content)
        started = time.monotonic()
        if media_type is None or len(content) > MAX_RECEIPT_IMAGE_BYTES:
            report.add_failure(expected, duration_ms=0)
            print(f"{name}: not an accepted image", file=out)
            continue
        try:
            output = recognizer.recognize(image=content, media_type=media_type)
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            report.add_failure(expected, duration_ms=duration_ms)
            print(f"{name}: recognizer failed ({type(error).__name__})", file=out)
            continue
        duration_ms = int((time.monotonic() - started) * 1000)
        selected = select_values(output, now=_photographed_at(entry, zone), timezone=timezone)
        outcomes = score(selected, expected)
        report.add(outcomes, duration_ms=duration_ms)
        summary = ", ".join(f"{field.value} {outcome.value}" for field, outcome in outcomes.items())
        print(f"{name}: {summary}", file=out)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a receipt recognizer.")
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args(argv)
    recognizer = build_recognizer(get_settings())
    if recognizer is None:
        print("no receipt recognizer is configured", file=sys.stderr)
        return 2
    try:
        timezone = Timezone(args.timezone)
        report = evaluate(recognizer, args.samples, timezone=timezone, out=sys.stdout)
    except (SampleError, ValueError, OSError) as error:
        print(f"evaluation failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
