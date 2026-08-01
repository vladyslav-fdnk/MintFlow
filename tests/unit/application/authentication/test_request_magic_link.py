from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

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
from mintflow.application.authentication.tokens import GeneratedToken, hash_token

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
RAW_TOKEN = "test-only-raw-token"


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


def build_use_case() -> tuple[RequestMagicLink, InMemoryChallengeStore, InMemoryEmailSender]:
    store = InMemoryChallengeStore()
    sender = InMemoryEmailSender()
    use_case = RequestMagicLink(
        challenge_store=store,
        email_sender=sender,
        link_builder=MagicLinkBuilder(
            web_origin="https://app.mintflow.test",
            allowed_return_targets=frozenset({"dashboard"}),
        ),
        clock=lambda: NOW,
        token_generator=lambda: GeneratedToken(raw=RAW_TOKEN, digest=hash_token(RAW_TOKEN)),
    )
    return use_case, store, sender


def test_persists_challenge_and_sends_typed_magic_link() -> None:
    use_case, store, sender = build_use_case()

    result = use_case.execute(
        submitted_email="Person@EXAMPLE.COM",
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
    use_case, store, sender = build_use_case()

    use_case.execute(submitted_email="new@example.com", return_target="dashboard")

    assert store.challenges[0].canonical_email == "new@example.com"
    assert sender.messages[0].recipient_email == "new@example.com"


def test_invalid_email_returns_generic_result_without_persisting_or_sending() -> None:
    use_case, store, sender = build_use_case()

    result = use_case.execute(submitted_email="not-an-email", return_target="dashboard")

    assert result == GENERIC_MAGIC_LINK_REQUEST_RESULT
    assert store.challenges == []
    assert sender.messages == []


def test_delivery_failure_returns_generic_result_after_challenge_is_persisted() -> None:
    store = InMemoryChallengeStore()
    use_case = RequestMagicLink(
        challenge_store=store,
        email_sender=FailingEmailSender(),
        link_builder=MagicLinkBuilder(
            web_origin="https://app.mintflow.test",
            allowed_return_targets=frozenset({"dashboard"}),
        ),
        clock=lambda: NOW,
        token_generator=lambda: GeneratedToken(raw=RAW_TOKEN, digest=hash_token(RAW_TOKEN)),
    )

    result = use_case.execute(submitted_email="person@example.com", return_target="dashboard")

    assert result == GENERIC_MAGIC_LINK_REQUEST_RESULT
    assert len(store.challenges) == 1


def test_rejects_unapproved_return_target_before_persisting_or_sending() -> None:
    use_case, store, sender = build_use_case()

    with pytest.raises(InvalidReturnTargetError):
        use_case.execute(
            submitted_email="person@example.com",
            return_target="https://attacker.example",
        )

    assert store.challenges == []
    assert sender.messages == []


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
