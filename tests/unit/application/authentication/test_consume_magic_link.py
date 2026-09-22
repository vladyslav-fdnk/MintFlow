from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mintflow.application.authentication import (
    AuthenticatedUserIdentity,
    ConsumeMagicLink,
    MagicLinkConsumptionResult,
    hash_token,
)
from mintflow.application.authentication.tokens import GeneratedToken
from mintflow.application.authentication.web_session import WebSession

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class RecordingConsumer:
    def __init__(self, result: MagicLinkConsumptionResult) -> None:
        self.result = result
        self.token_hash: bytes | None = None
        self.consumed_at: datetime | None = None
        self.session: WebSession | None = None

    def consume(
        self, *, token_hash: bytes, consumed_at: datetime, session_factory: object
    ) -> MagicLinkConsumptionResult:
        self.token_hash = token_hash
        self.consumed_at = consumed_at
        if self.result.identity is not None:
            assert callable(session_factory)
            self.session = session_factory(self.result.identity.user_id)
        return self.result


def test_hashes_presented_token_and_returns_authenticated_result() -> None:
    expected = MagicLinkConsumptionResult(
        identity=AuthenticatedUserIdentity(user_id=uuid4()),
        return_target="dashboard",
    )
    consumer = RecordingConsumer(expected)

    result = ConsumeMagicLink(
        challenge_consumer=consumer,
        clock=lambda: NOW,
        token_generator=lambda: GeneratedToken(
            raw="session-secret", digest=hash_token("session-secret")
        ),
    ).execute(token="raw-secret")

    assert result.identity == expected.identity
    assert result.return_target == expected.return_target
    assert result.session_secret == "session-secret"
    assert consumer.token_hash == hash_token("raw-secret")
    assert consumer.consumed_at == NOW
    assert consumer.session is not None
    assert consumer.session.secret_hash == hash_token("session-secret")


def test_rejects_naive_clock() -> None:
    consumer = RecordingConsumer(MagicLinkConsumptionResult(identity=None, return_target=None))

    with pytest.raises(ValueError, match="timezone-aware"):
        ConsumeMagicLink(
            challenge_consumer=consumer,
            clock=lambda: datetime(2026, 8, 1, 12, 0),
        ).execute(token="raw-secret")
