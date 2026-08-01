from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from mintflow.application.authentication.email import InvalidEmailError, normalize_email
from mintflow.application.authentication.magic_link import MagicLinkBuilder
from mintflow.application.authentication.tokens import GeneratedToken, generate_token

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


class EmailSender(Protocol):
    def send_magic_link(self, message: MagicLinkMessage) -> None: ...


class EmailDeliveryError(RuntimeError):
    """An expected delivery-provider failure safe to map to a generic result."""


class RequestMagicLink:
    def __init__(
        self,
        *,
        challenge_store: LoginChallengeStore,
        email_sender: EmailSender,
        link_builder: MagicLinkBuilder,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_generator: Callable[[], GeneratedToken] = generate_token,
    ) -> None:
        self._challenge_store = challenge_store
        self._email_sender = email_sender
        self._link_builder = link_builder
        self._clock = clock
        self._token_generator = token_generator

    def execute(self, *, submitted_email: str, return_target: str) -> MagicLinkRequestResult:
        try:
            email = normalize_email(submitted_email)
        except InvalidEmailError:
            return GENERIC_MAGIC_LINK_REQUEST_RESULT
        issued_at = self._clock()
        if issued_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        issued_at = issued_at.astimezone(UTC)
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
