from datetime import UTC, datetime, timedelta

from mintflow.application.authentication.rate_limit import (
    RateLimitDigester,
    RateLimitDimension,
    fixed_window_start,
)


def test_fixed_window_start_uses_utc_quarter_hour() -> None:
    now = datetime.fromisoformat("2026-08-01T14:29:59.999999+02:00")

    assert fixed_window_start(now) == datetime(2026, 8, 1, 12, 15, tzinfo=UTC)


def test_fixed_window_changes_at_exact_boundary() -> None:
    before = datetime(2026, 8, 1, 12, 14, 59, tzinfo=UTC)

    assert fixed_window_start(before) == datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert fixed_window_start(before + timedelta(seconds=1)) == datetime(
        2026, 8, 1, 12, 15, tzinfo=UTC
    )


def test_digest_is_keyed_deterministic_namespaced_and_hides_source() -> None:
    first = RateLimitDigester(b"first-secret")
    second = RateLimitDigester(b"second-secret")
    raw = "person@example.com"

    email_digest = first.digest(dimension=RateLimitDimension.EMAIL_DELIVERY, key=raw)

    assert email_digest == first.digest(dimension=RateLimitDimension.EMAIL_DELIVERY, key=raw)
    assert email_digest != second.digest(dimension=RateLimitDimension.EMAIL_DELIVERY, key=raw)
    assert email_digest != first.digest(dimension=RateLimitDimension.NETWORK_REQUEST, key=raw)
    assert len(email_digest) == 32
    assert raw.encode() not in email_digest
