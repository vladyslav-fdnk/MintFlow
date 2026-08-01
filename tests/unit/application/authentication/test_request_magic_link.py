from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from mintflow.application.authentication.audit import AuthenticationAuditRecord
from mintflow.application.authentication.login_challenge import (
    GENERIC_MAGIC_LINK_REQUEST_RESULT,
    EmailDeliveryError,
    LoginChallenge,
    MagicLinkMessage,
    RequestMagicLink,
)
from mintflow.application.authentication.magic_link import (
    InvalidMagicLinkConfigurationError,
    InvalidReturnTargetError,
    MagicLinkBuilder,
)
from mintflow.application.authentication.rate_limit import (
    RateLimitDigester,
    RateLimitDimension,
    RateLimitReservation,
)
from mintflow.application.authentication.tokens import GeneratedToken, hash_token

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
RAW_TOKEN = "test-only-raw-token"
NETWORK_SOURCE = "192.0.2.1"


class RecordingAuditAppender:
    def __init__(self) -> None:
        self.records: list[AuthenticationAuditRecord] = []

    def append(self, record: AuthenticationAuditRecord) -> None:
        self.records.append(record)


class InMemoryChallengeStore:
    def __init__(self) -> None:
        self.challenges: list[LoginChallenge] = []

    def add(self, challenge: LoginChallenge) -> None:
        self.challenges.append(challenge)


class InMemoryEmailSender:
    def __init__(self) -> None:
        self.messages: list[MagicLinkMessage] = []

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        self.messages.append(message)


class FailingEmailSender:
    def send_magic_link(self, message: MagicLinkMessage) -> None:
        raise EmailDeliveryError("provider unavailable")


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self.reservations: list[RateLimitReservation] = []
        self.rejected_dimensions: set[RateLimitDimension] = set()

    def reserve(self, reservation: RateLimitReservation) -> bool:
        self.reservations.append(reservation)
        return reservation.dimension not in self.rejected_dimensions


class ThresholdRateLimiter(InMemoryRateLimiter):
    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[tuple[RateLimitDimension, bytes, datetime], int] = {}

    def reserve(self, reservation: RateLimitReservation) -> bool:
        self.reservations.append(reservation)
        key = (
            reservation.dimension,
            reservation.key_digest,
            reservation.window_started_at,
        )
        count = self.counts.get(key, 0)
        if count >= reservation.limit:
            return False
        self.counts[key] = count + 1
        return True


def build_use_case() -> tuple[
    RequestMagicLink,
    InMemoryChallengeStore,
    InMemoryEmailSender,
    InMemoryRateLimiter,
    RecordingAuditAppender,
]:
    store = InMemoryChallengeStore()
    sender = InMemoryEmailSender()
    limiter = InMemoryRateLimiter()
    audit = RecordingAuditAppender()
    use_case = RequestMagicLink(
        challenge_store=store,
        email_sender=sender,
        link_builder=MagicLinkBuilder(
            web_origin="https://app.mintflow.test",
            allowed_return_targets=frozenset({"dashboard"}),
        ),
        rate_limiter=limiter,
        rate_limit_digester=RateLimitDigester(b"test-rate-limit-key"),
        audit_appender=audit,
        clock=lambda: NOW,
        token_generator=lambda: GeneratedToken(raw=RAW_TOKEN, digest=hash_token(RAW_TOKEN)),
    )
    return use_case, store, sender, limiter, audit


def test_persists_challenge_and_sends_typed_magic_link() -> None:
    use_case, store, sender, limiter, audit = build_use_case()

    result = use_case.execute(
        submitted_email="Person@EXAMPLE.COM",
        normalized_network_source=NETWORK_SOURCE,
        return_target="dashboard",
    )

    assert result == GENERIC_MAGIC_LINK_REQUEST_RESULT
    assert len(store.challenges) == 1
    challenge = store.challenges[0]
    assert challenge.canonical_email == "Person@example.com"
    assert challenge.issued_at == NOW
    assert challenge.expires_at == NOW + timedelta(minutes=15)
    assert challenge.consumed_at is None
    assert challenge.return_target == "dashboard"
    assert challenge.token_hash == hash_token(RAW_TOKEN)
    assert RAW_TOKEN.encode() not in challenge.token_hash
    assert len(audit.records) == 1
    assert audit.records[0].outcome.value == "succeeded"
    assert audit.records[0].subject_record_id == challenge.id
    assert [item.dimension for item in limiter.reservations] == [
        RateLimitDimension.NETWORK_REQUEST,
        RateLimitDimension.EMAIL_DELIVERY,
    ]

    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert message.recipient_email == "Person@EXAMPLE.COM"
    link = urlsplit(message.magic_link)
    assert (link.scheme, link.netloc, link.path) == (
        "https",
        "app.mintflow.test",
        "/auth/magic-link",
    )
    assert parse_qs(link.query) == {
        "token": [RAW_TOKEN],
        "return_target": ["dashboard"],
    }


def test_issuance_does_not_require_or_query_a_user() -> None:
    use_case, store, sender, _, _ = build_use_case()

    use_case.execute(
        submitted_email="new@example.com",
        normalized_network_source=NETWORK_SOURCE,
        return_target="dashboard",
    )

    assert store.challenges[0].canonical_email == "new@example.com"
    assert sender.messages[0].recipient_email == "new@example.com"


def test_invalid_email_returns_generic_result_without_persisting_or_sending() -> None:
    use_case, store, sender, limiter, audit = build_use_case()

    result = use_case.execute(
        submitted_email="not-an-email",
        normalized_network_source=NETWORK_SOURCE,
        return_target="dashboard",
    )

    assert result == GENERIC_MAGIC_LINK_REQUEST_RESULT
    assert store.challenges == []
    assert sender.messages == []
    assert limiter.reservations == []
    assert len(audit.records) == 1
    assert audit.records[0].outcome.value == "failed"
    assert audit.records[0].user_id is None
    assert audit.records[0].subject_record_id is None


def test_delivery_failure_returns_generic_result_after_challenge_is_persisted() -> None:
    store = InMemoryChallengeStore()
    limiter = ThresholdRateLimiter()
    use_case = RequestMagicLink(
        challenge_store=store,
        email_sender=FailingEmailSender(),
        link_builder=MagicLinkBuilder(
            web_origin="https://app.mintflow.test",
            allowed_return_targets=frozenset({"dashboard"}),
        ),
        rate_limiter=limiter,
        rate_limit_digester=RateLimitDigester(b"test-rate-limit-key"),
        audit_appender=RecordingAuditAppender(),
        clock=lambda: NOW,
        token_generator=lambda: GeneratedToken(raw=RAW_TOKEN, digest=hash_token(RAW_TOKEN)),
    )

    results = [
        use_case.execute(
            submitted_email="person@example.com",
            normalized_network_source=f"192.0.2.{index}",
            return_target="dashboard",
        )
        for index in range(4)
    ]

    assert results == [GENERIC_MAGIC_LINK_REQUEST_RESULT] * 4
    assert len(store.challenges) == 3
    assert len(limiter.reservations) == 8


def test_rejects_unapproved_return_target_before_rate_limiting_persisting_or_sending() -> None:
    use_case, store, sender, limiter, _ = build_use_case()

    with pytest.raises(InvalidReturnTargetError):
        use_case.execute(
            submitted_email="person@example.com",
            normalized_network_source=NETWORK_SOURCE,
            return_target="https://attacker.example",
        )

    assert store.challenges == []
    assert sender.messages == []
    assert limiter.reservations == []


@pytest.mark.parametrize(
    "rejected_dimension",
    [RateLimitDimension.NETWORK_REQUEST, RateLimitDimension.EMAIL_DELIVERY],
)
def test_rate_limit_rejection_returns_generic_result_without_issuing(
    rejected_dimension: RateLimitDimension,
) -> None:
    use_case, store, sender, limiter, _ = build_use_case()
    limiter.rejected_dimensions.add(rejected_dimension)

    result = use_case.execute(
        submitted_email="person@example.com",
        normalized_network_source=NETWORK_SOURCE,
        return_target="dashboard",
    )

    assert result == GENERIC_MAGIC_LINK_REQUEST_RESULT
    assert store.challenges == []
    assert sender.messages == []
    if rejected_dimension is RateLimitDimension.NETWORK_REQUEST:
        assert len(limiter.reservations) == 1


@pytest.mark.parametrize(
    ("dimension", "limit"),
    [
        (RateLimitDimension.EMAIL_DELIVERY, 3),
        (RateLimitDimension.NETWORK_REQUEST, 30),
    ],
)
def test_issuance_enforces_exact_dimension_threshold(
    dimension: RateLimitDimension, limit: int
) -> None:
    store = InMemoryChallengeStore()
    sender = InMemoryEmailSender()
    limiter = ThresholdRateLimiter()
    use_case = RequestMagicLink(
        challenge_store=store,
        email_sender=sender,
        link_builder=MagicLinkBuilder(
            web_origin="https://app.mintflow.test",
            allowed_return_targets=frozenset({"dashboard"}),
        ),
        rate_limiter=limiter,
        rate_limit_digester=RateLimitDigester(b"test-rate-limit-key"),
        audit_appender=RecordingAuditAppender(),
        clock=lambda: NOW,
        token_generator=lambda: GeneratedToken(raw=RAW_TOKEN, digest=hash_token(RAW_TOKEN)),
    )

    for index in range(limit + 1):
        email = "person@example.com"
        network = NETWORK_SOURCE
        if dimension is RateLimitDimension.EMAIL_DELIVERY:
            network = f"192.0.2.{index}"
        else:
            email = f"person{index}@example.com"
        use_case.execute(
            submitted_email=email,
            normalized_network_source=network,
            return_target="dashboard",
        )

    assert len(store.challenges) == limit
    assert len(sender.messages) == limit


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.mintflow.test/extra",
        "https://app.mintflow.test?token=secret",
        "https://user:password@app.mintflow.test",
        "javascript:alert(1)",
    ],
)
def test_rejects_values_that_are_not_clean_configured_origins(origin: str) -> None:
    with pytest.raises(InvalidMagicLinkConfigurationError):
        MagicLinkBuilder(web_origin=origin, allowed_return_targets=frozenset({"dashboard"}))
