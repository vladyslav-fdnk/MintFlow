from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from mintflow.application.authentication.rate_limit import (
    RATE_LIMIT_RETENTION,
    RateLimitDigester,
    RateLimitDimension,
    RateLimitReservation,
    make_reservation,
)
from mintflow.infrastructure.persistence.authentication_rate_limits import (
    PostgreSQLAuthenticationRateLimiter,
)
from mintflow.infrastructure.persistence.models import AuthenticationRateLimitBucketRecord

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 12, 7, tzinfo=UTC)


def reservation(
    *,
    dimension: RateLimitDimension = RateLimitDimension.EMAIL_DELIVERY,
    key_digest: bytes = b"e" * 32,
    now: datetime = NOW,
    limit: int = 3,
) -> RateLimitReservation:
    return make_reservation(
        dimension=dimension,
        key_digest=key_digest,
        now=now,
        limit=limit,
    )


def test_creates_bucket_and_allows_exact_threshold(db_session: Session) -> None:
    limiter = PostgreSQLAuthenticationRateLimiter(db_session)
    item = reservation()

    assert [limiter.reserve(item) for _ in range(3)] == [True, True, True]
    assert limiter.reserve(item) is False

    record = db_session.get(
        AuthenticationRateLimitBucketRecord,
        (item.dimension.value, item.key_digest, item.window_started_at),
    )
    assert record is not None
    assert record.count == 3
    assert record.expires_at == item.window_started_at + RATE_LIMIT_RETENTION


def test_dimensions_keys_and_windows_are_independent(db_session: Session) -> None:
    limiter = PostgreSQLAuthenticationRateLimiter(db_session)
    items = [
        reservation(limit=1),
        reservation(dimension=RateLimitDimension.NETWORK_REQUEST, limit=1),
        reservation(key_digest=b"f" * 32, limit=1),
        reservation(now=NOW + timedelta(minutes=15), limit=1),
    ]

    assert all(limiter.reserve(item) for item in items)
    assert all(not limiter.reserve(item) for item in items)
    assert len(db_session.scalars(select(AuthenticationRateLimitBucketRecord)).all()) == 4


def test_raw_email_and_ip_are_never_persisted(db_session: Session) -> None:
    limiter = PostgreSQLAuthenticationRateLimiter(db_session)
    digester = RateLimitDigester(b"test-rate-limit-secret")
    email = "person@example.com"
    network = "192.0.2.10"

    assert limiter.reserve(
        reservation(
            key_digest=digester.digest(dimension=RateLimitDimension.EMAIL_DELIVERY, key=email)
        )
    )
    assert limiter.reserve(
        reservation(
            dimension=RateLimitDimension.NETWORK_REQUEST,
            key_digest=digester.digest(dimension=RateLimitDimension.NETWORK_REQUEST, key=network),
        )
    )

    records = db_session.scalars(select(AuthenticationRateLimitBucketRecord)).all()
    persisted = b" ".join(record.key_digest for record in records)
    assert email.encode() not in persisted
    assert network.encode() not in persisted


def test_concurrent_reservations_allow_only_threshold(engine: Engine) -> None:
    item = reservation(limit=3)
    barrier = Barrier(8)

    def reserve_once() -> bool:
        with Session(engine) as session:
            limiter = PostgreSQLAuthenticationRateLimiter(session)
            barrier.wait()
            return limiter.reserve(item)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: reserve_once(), range(8)))

    assert results.count(True) == 3
    assert results.count(False) == 5
    with Session(engine) as session:
        record = session.get(
            AuthenticationRateLimitBucketRecord,
            (item.dimension.value, item.key_digest, item.window_started_at),
        )
        assert record is not None
        assert record.count == 3
