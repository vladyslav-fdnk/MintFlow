from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from mintflow.application.authentication.audit import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
    AuthenticationAuditRecord,
    authentication_audit_cutoff,
    is_authentication_audit_record_eligible,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_closed_event_and_outcome_vocabularies_reject_unknown_values() -> None:
    assert (
        AuthenticationAuditEventType("login_succeeded")
        is AuthenticationAuditEventType.LOGIN_SUCCEEDED
    )
    assert AuthenticationAuditOutcome("failed") is AuthenticationAuditOutcome.FAILED
    with pytest.raises(ValueError):
        AuthenticationAuditEventType("arbitrary")
    with pytest.raises(ValueError):
        AuthenticationAuditOutcome("unknown")


def test_record_contains_only_approved_minimal_fields() -> None:
    record = AuthenticationAuditRecord(
        occurred_at=NOW,
        event_type=AuthenticationAuditEventType.LOGIN_FAILED,
        outcome=AuthenticationAuditOutcome.FAILED,
    )

    assert set(record.__dataclass_fields__) == {
        "id",
        "occurred_at",
        "event_type",
        "outcome",
        "user_id",
        "subject_record_id",
    }
    assert record.user_id is None
    assert record.subject_record_id is None


def test_retention_cutoff_and_exact_boundary() -> None:
    cutoff = authentication_audit_cutoff(now=NOW)

    assert cutoff == NOW - timedelta(days=90)
    assert is_authentication_audit_record_eligible(occurred_at=cutoff, cutoff=cutoff)
    assert is_authentication_audit_record_eligible(
        occurred_at=cutoff - timedelta(microseconds=1), cutoff=cutoff
    )
    assert not is_authentication_audit_record_eligible(
        occurred_at=cutoff + timedelta(microseconds=1), cutoff=cutoff
    )


def test_record_rejects_naive_occurrence_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AuthenticationAuditRecord(
            id=UUID(int=1),
            occurred_at=datetime(2026, 8, 1),
            event_type=AuthenticationAuditEventType.LOGIN_FAILED,
            outcome=AuthenticationAuditOutcome.FAILED,
        )
