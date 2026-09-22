from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

LOGIN_CHALLENGE_RETENTION = timedelta(days=30)
WEB_SESSION_RETENTION = timedelta(days=30)
AUTHENTICATION_AUDIT_RETENTION = timedelta(days=90)
MAX_RATE_LIMIT_RETENTION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class AuthenticationRetentionCutoffs:
    login_challenge_consumed_at: datetime
    login_challenge_expires_at: datetime
    web_session_revoked_at: datetime
    web_session_expires_at: datetime
    rate_limit_expired_at: datetime
    rate_limit_maximum_age_at: datetime
    authentication_audit_occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticationRetentionResult:
    login_challenges_deleted: int
    web_sessions_deleted: int
    rate_limit_buckets_deleted: int
    authentication_audit_records_deleted: int

    @property
    def total_deleted(self) -> int:
        return sum(asdict(self).values())

    def aggregate_counts(self) -> dict[str, int]:
        return {**asdict(self), "total_deleted": self.total_deleted}


class AuthenticationRetentionRepository(Protocol):
    def delete_login_challenges(
        self, *, consumed_cutoff: datetime, expired_cutoff: datetime, batch_size: int
    ) -> int: ...

    def delete_web_sessions(
        self, *, revoked_cutoff: datetime, expired_cutoff: datetime, batch_size: int
    ) -> int: ...

    def delete_rate_limit_buckets(
        self, *, expired_at: datetime, maximum_age_at: datetime, batch_size: int
    ) -> int: ...

    def delete_authentication_audit_records(
        self, *, occurred_cutoff: datetime, batch_size: int
    ) -> int: ...


def authentication_retention_cutoffs(*, now: datetime) -> AuthenticationRetentionCutoffs:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    utc_now = now.astimezone(UTC)
    return AuthenticationRetentionCutoffs(
        login_challenge_consumed_at=utc_now - LOGIN_CHALLENGE_RETENTION,
        login_challenge_expires_at=utc_now - LOGIN_CHALLENGE_RETENTION,
        web_session_revoked_at=utc_now - WEB_SESSION_RETENTION,
        web_session_expires_at=utc_now - WEB_SESSION_RETENTION,
        rate_limit_expired_at=utc_now,
        rate_limit_maximum_age_at=utc_now - MAX_RATE_LIMIT_RETENTION,
        authentication_audit_occurred_at=utc_now - AUTHENTICATION_AUDIT_RETENTION,
    )


def validate_authentication_retention_batch_size(batch_size: int) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    return batch_size


def is_at_or_before_retention_cutoff(*, timestamp: datetime, cutoff: datetime) -> bool:
    if timestamp.utcoffset() is None or cutoff.utcoffset() is None:
        raise ValueError("timestamp and cutoff must be timezone-aware")
    return timestamp.astimezone(UTC) <= cutoff.astimezone(UTC)


class CleanUpAuthenticationRetention:
    def __init__(
        self,
        *,
        repository: AuthenticationRetentionRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, *, batch_size: int) -> AuthenticationRetentionResult:
        validated_batch_size = validate_authentication_retention_batch_size(batch_size)
        cutoffs = authentication_retention_cutoffs(now=self._clock())
        return AuthenticationRetentionResult(
            login_challenges_deleted=self._repository.delete_login_challenges(
                consumed_cutoff=cutoffs.login_challenge_consumed_at,
                expired_cutoff=cutoffs.login_challenge_expires_at,
                batch_size=validated_batch_size,
            ),
            web_sessions_deleted=self._repository.delete_web_sessions(
                revoked_cutoff=cutoffs.web_session_revoked_at,
                expired_cutoff=cutoffs.web_session_expires_at,
                batch_size=validated_batch_size,
            ),
            rate_limit_buckets_deleted=self._repository.delete_rate_limit_buckets(
                expired_at=cutoffs.rate_limit_expired_at,
                maximum_age_at=cutoffs.rate_limit_maximum_age_at,
                batch_size=validated_batch_size,
            ),
            authentication_audit_records_deleted=(
                self._repository.delete_authentication_audit_records(
                    occurred_cutoff=cutoffs.authentication_audit_occurred_at,
                    batch_size=validated_batch_size,
                )
            ),
        )
