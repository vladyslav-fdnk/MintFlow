from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from mintflow.application.authentication.email import InvalidEmailError, normalize_email
from mintflow.application.authentication.magic_link import MagicLinkBuilder
from mintflow.application.authentication.rate_limit import (
    EMAIL_DELIVERY_LIMIT,
    NETWORK_REQUEST_LIMIT,
    AuthenticationRateLimiter,
    RateLimitDigester,
    RateLimitDimension,
    make_reservation,
)
from mintflow.application.authentication.tokens import GeneratedToken, generate_token, hash_token
from mintflow.application.authentication.web_session import WebSession, new_web_session

LOGIN_CHALLENGE_LIFETIME = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    id: UUID
    canonical_email: str
    token_hash: bytes
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    return_target: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("issued_at", self.issued_at),
            ("expires_at", self.expires_at),
        ):
            if value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.consumed_at is not None and self.consumed_at.utcoffset() is None:
            raise ValueError("consumed_at must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")


@dataclass(frozen=True, slots=True)
class MagicLinkMessage:
    recipient_email: str
    magic_link: str


@dataclass(frozen=True, slots=True)
class MagicLinkRequestResult:
    message: str


GENERIC_MAGIC_LINK_REQUEST_RESULT = MagicLinkRequestResult(
    message="If the address can receive email, a sign-in link will arrive shortly."
)


class LoginChallengeStore(Protocol):
    def add(self, challenge: LoginChallenge) -> None: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedUserIdentity:
    user_id: UUID


@dataclass(frozen=True, slots=True)
class MagicLinkConsumptionResult:
    identity: AuthenticatedUserIdentity | None
    return_target: str | None
    session_secret: str | None = None

    @property
    def authenticated(self) -> bool:
        return self.identity is not None


INVALID_MAGIC_LINK_CONSUMPTION_RESULT = MagicLinkConsumptionResult(
    identity=None,
    return_target=None,
)


class LoginChallengeConsumer(Protocol):
    def consume(
        self,
        *,
        token_hash: bytes,
        consumed_at: datetime,
        session_factory: Callable[[UUID], WebSession],
    ) -> MagicLinkConsumptionResult: ...


class EmailSender(Protocol):
    def send_magic_link(self, message: MagicLinkMessage) -> None: ...


class EmailDeliveryError(RuntimeError):
    """An expected delivery-provider failure safe to map to a generic result."""


class ConsumeMagicLink:
    def __init__(
        self,
        *,
        challenge_consumer: LoginChallengeConsumer,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_generator: Callable[[], GeneratedToken] = generate_token,
    ) -> None:
        self._challenge_consumer = challenge_consumer
        self._clock = clock
        self._token_generator = token_generator

    def execute(self, *, token: str) -> MagicLinkConsumptionResult:
        consumed_at = self._clock()
        if consumed_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        raw_session_secret: str | None = None

        def session_factory(user_id: UUID) -> WebSession:
            nonlocal raw_session_secret
            session, raw_session_secret = new_web_session(
                user_id=user_id,
                issued_at=consumed_at,
                token_generator=self._token_generator,
            )
            return session

        result = self._challenge_consumer.consume(
            token_hash=hash_token(token),
            consumed_at=consumed_at.astimezone(UTC),
            session_factory=session_factory,
        )
        if not result.authenticated:
            return result
        if raw_session_secret is None:
            raise RuntimeError("authenticated consumption did not create a Web session")
        return replace(result, session_secret=raw_session_secret)


class RequestMagicLink:
    def __init__(
        self,
        *,
        challenge_store: LoginChallengeStore,
        email_sender: EmailSender,
        link_builder: MagicLinkBuilder,
        rate_limiter: AuthenticationRateLimiter,
        rate_limit_digester: RateLimitDigester,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_generator: Callable[[], GeneratedToken] = generate_token,
    ) -> None:
        self._challenge_store = challenge_store
        self._email_sender = email_sender
        self._link_builder = link_builder
        self._rate_limiter = rate_limiter
        self._rate_limit_digester = rate_limit_digester
        self._clock = clock
        self._token_generator = token_generator

    def execute(
        self, *, submitted_email: str, normalized_network_source: str, return_target: str
    ) -> MagicLinkRequestResult:
        try:
            email = normalize_email(submitted_email)
        except InvalidEmailError:
            return GENERIC_MAGIC_LINK_REQUEST_RESULT
        self._link_builder.validate_return_target(return_target)
        issued_at = self._clock()
        if issued_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        issued_at = issued_at.astimezone(UTC)
        network_allowed = self._rate_limiter.reserve(
            make_reservation(
                dimension=RateLimitDimension.NETWORK_REQUEST,
                key_digest=self._rate_limit_digester.digest(
                    dimension=RateLimitDimension.NETWORK_REQUEST,
                    key=normalized_network_source,
                ),
                now=issued_at,
                limit=NETWORK_REQUEST_LIMIT,
            )
        )
        if not network_allowed:
            return GENERIC_MAGIC_LINK_REQUEST_RESULT
        email_allowed = self._rate_limiter.reserve(
            make_reservation(
                dimension=RateLimitDimension.EMAIL_DELIVERY,
                key_digest=self._rate_limit_digester.digest(
                    dimension=RateLimitDimension.EMAIL_DELIVERY,
                    key=email.canonical_email,
                ),
                now=issued_at,
                limit=EMAIL_DELIVERY_LIMIT,
            )
        )
        if not email_allowed:
            return GENERIC_MAGIC_LINK_REQUEST_RESULT
        generated_token = self._token_generator()
        magic_link = self._link_builder.build(
            token=generated_token.raw,
            return_target=return_target,
        )
        challenge = LoginChallenge(
            id=uuid4(),
            canonical_email=email.canonical_email,
            token_hash=generated_token.digest,
            issued_at=issued_at,
            expires_at=issued_at + LOGIN_CHALLENGE_LIFETIME,
            consumed_at=None,
            return_target=return_target,
        )
        self._challenge_store.add(challenge)
        try:
            self._email_sender.send_magic_link(
                MagicLinkMessage(recipient_email=email.display_email, magic_link=magic_link)
            )
        except EmailDeliveryError:
            pass
        return GENERIC_MAGIC_LINK_REQUEST_RESULT
