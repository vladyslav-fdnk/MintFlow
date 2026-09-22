from datetime import UTC, datetime, timedelta, timezone

import pytest

from mintflow.application.authentication.retention import (
    AuthenticationRetentionResult,
    CleanUpAuthenticationRetention,
    authentication_retention_cutoffs,
    is_at_or_before_retention_cutoff,
    validate_authentication_retention_batch_size,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class RecordingRetentionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def delete_login_challenges(
        self, *, consumed_cutoff: datetime, expired_cutoff: datetime, batch_size: int
    ) -> int:
        self.calls.append(("login_challenges", locals()))
        return 1

    def delete_web_sessions(
        self, *, revoked_cutoff: datetime, expired_cutoff: datetime, batch_size: int
    ) -> int:
        self.calls.append(("web_sessions", locals()))
        return 2

    def delete_rate_limit_buckets(
        self, *, expired_at: datetime, maximum_age_at: datetime, batch_size: int
    ) -> int:
        self.calls.append(("rate_limit_buckets", locals()))
        return 3

    def delete_authentication_audit_records(
        self, *, occurred_cutoff: datetime, batch_size: int
    ) -> int:
        self.calls.append(("authentication_audit_records", locals()))
        return 4


def test_calculates_every_approved_retention_cutoff() -> None:
    cutoffs = authentication_retention_cutoffs(now=NOW)

    assert cutoffs.login_challenge_consumed_at == NOW - timedelta(days=30)
    assert cutoffs.login_challenge_expires_at == NOW - timedelta(days=30)
    assert cutoffs.web_session_revoked_at == NOW - timedelta(days=30)
    assert cutoffs.web_session_expires_at == NOW - timedelta(days=30)
    assert cutoffs.rate_limit_expired_at == NOW
    assert cutoffs.rate_limit_maximum_age_at == NOW - timedelta(hours=24)
    assert cutoffs.authentication_audit_occurred_at == NOW - timedelta(days=90)


def test_cutoffs_normalize_timezone_aware_clock_to_utc() -> None:
    local_now = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))

    cutoffs = authentication_retention_cutoffs(now=local_now)

    assert cutoffs.rate_limit_expired_at == NOW
    assert cutoffs.rate_limit_expired_at.tzinfo is UTC


def test_cutoffs_reject_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        authentication_retention_cutoffs(now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "cutoff_name",
    [
        "login_challenge_consumed_at",
        "login_challenge_expires_at",
        "web_session_revoked_at",
        "web_session_expires_at",
        "rate_limit_expired_at",
        "rate_limit_maximum_age_at",
        "authentication_audit_occurred_at",
    ],
)
@pytest.mark.parametrize(
    ("offset", "eligible"),
    [
        (timedelta(microseconds=-1), True),
        (timedelta(0), True),
        (timedelta(microseconds=1), False),
    ],
)
def test_before_exactly_at_and_after_each_boundary(
    cutoff_name: str, offset: timedelta, eligible: bool
) -> None:
    cutoff = getattr(authentication_retention_cutoffs(now=NOW), cutoff_name)

    assert is_at_or_before_retention_cutoff(timestamp=cutoff + offset, cutoff=cutoff) is eligible


def test_boundary_comparison_requires_aware_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_at_or_before_retention_cutoff(timestamp=NOW.replace(tzinfo=None), cutoff=NOW)


@pytest.mark.parametrize("batch_size", [1, 10, 1_000])
def test_accepts_positive_batch_sizes(batch_size: int) -> None:
    assert validate_authentication_retention_batch_size(batch_size) == batch_size


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5, "1"])
def test_rejects_invalid_batch_sizes(batch_size: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        validate_authentication_retention_batch_size(batch_size)  # type: ignore[arg-type]


def test_service_uses_one_clock_value_and_reports_aggregate_counts_only() -> None:
    repository = RecordingRetentionRepository()
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return NOW

    result = CleanUpAuthenticationRetention(repository=repository, clock=clock).execute(
        batch_size=7
    )

    assert clock_calls == 1
    assert result == AuthenticationRetentionResult(1, 2, 3, 4)
    assert result.aggregate_counts() == {
        "login_challenges_deleted": 1,
        "web_sessions_deleted": 2,
        "rate_limit_buckets_deleted": 3,
        "authentication_audit_records_deleted": 4,
        "total_deleted": 10,
    }
    assert all(call[1]["batch_size"] == 7 for call in repository.calls)
    output = str(result.aggregate_counts()).lower()
    assert all(
        forbidden not in output
        for forbidden in ("email", "ip", "token", "hash", "secret", "cookie", "subject")
    )
