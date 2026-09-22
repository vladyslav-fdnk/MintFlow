import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

RATE_LIMIT_WINDOW = timedelta(minutes=15)
RATE_LIMIT_RETENTION = timedelta(hours=24)
EMAIL_DELIVERY_LIMIT = 3
NETWORK_REQUEST_LIMIT = 30


class RateLimitDimension(StrEnum):
    EMAIL_DELIVERY = "email_delivery"
    NETWORK_REQUEST = "network_request"


@dataclass(frozen=True, slots=True)
class RateLimitReservation:
    dimension: RateLimitDimension
    key_digest: bytes
    window_started_at: datetime
    expires_at: datetime
    limit: int


class AuthenticationRateLimiter(Protocol):
    def reserve(self, reservation: RateLimitReservation) -> bool: ...


def fixed_window_start(now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    utc_now = now.astimezone(UTC)
    minute = utc_now.minute - utc_now.minute % 15
    return utc_now.replace(minute=minute, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class RateLimitDigester:
    secret_key: bytes

    def __post_init__(self) -> None:
        if not self.secret_key:
            raise ValueError("rate-limit digest key must not be empty")

    def digest(self, *, dimension: RateLimitDimension, key: str) -> bytes:
        namespaced_key = f"{dimension.value}\0{key}".encode()
        return hmac.new(self.secret_key, namespaced_key, hashlib.sha256).digest()


def make_reservation(
    *,
    dimension: RateLimitDimension,
    key_digest: bytes,
    now: datetime,
    limit: int,
) -> RateLimitReservation:
    window_started_at = fixed_window_start(now)
    return RateLimitReservation(
        dimension=dimension,
        key_digest=key_digest,
        window_started_at=window_started_at,
        expires_at=window_started_at + RATE_LIMIT_RETENTION,
        limit=limit,
    )
