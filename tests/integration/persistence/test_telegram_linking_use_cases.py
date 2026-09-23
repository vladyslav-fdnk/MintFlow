import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.telegram import (
    ClaimTelegramLink,
    ConfirmTelegramLink,
    GetTelegramConnection,
    GetTelegramLinkStatus,
    IssueTelegramLinkChallenge,
    ResolveTelegramUser,
    TelegramLinkState,
    UnlinkTelegram,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyUserRepository,
)
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
OWNER_TG = 111_111
INTERCEPTOR_TG = 999_999


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _account(session: Session) -> AuthenticatedWebSession:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=1))
    session.add(user)
    session.commit()
    web_session = WebSessionRecord(
        user_id=user.id,
        secret_hash=hashlib.sha256(uuid4().bytes).digest(),
        issued_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=1),
    )
    session.add(web_session)
    session.commit()
    return AuthenticatedWebSession(session_id=web_session.id, user_id=user.id)


class Linking:
    def __init__(self, session: Session) -> None:
        self.clock = Clock()
        repository = SqlAlchemyTelegramLinkRepository(session)
        self.issue = IssueTelegramLinkChallenge(
            repository=repository, bot_username="mintflow_test_bot", clock=self.clock
        )
        self.claim = ClaimTelegramLink(repository=repository, clock=self.clock)
        self.status = GetTelegramLinkStatus(repository=repository, clock=self.clock)
        self.confirm = ConfirmTelegramLink(repository=repository, clock=self.clock)
        self.connection = GetTelegramConnection(repository=repository)
        self.unlink = UnlinkTelegram(repository=repository, clock=self.clock)
        self.resolve = ResolveTelegramUser(
            repository=repository, user_repository=SqlAlchemyUserRepository(session)
        )

    def token_of(self, deep_link: str) -> str:
        return deep_link.split("?start=", 1)[1]


def test_full_ceremony_through_the_use_cases(db_session: Session) -> None:
    linking = Linking(db_session)
    owner = _account(db_session)

    issued = linking.issue.execute(session=owner)
    waiting = linking.status.execute(challenge_id=issued.challenge_id, session=owner)
    linking.clock.advance(minutes=1)
    claimed = linking.claim.execute(
        payload=linking.token_of(issued.deep_link),
        telegram_user_id=OWNER_TG,
        display_name="Ada (@ada)",
    )
    pending = linking.status.execute(challenge_id=issued.challenge_id, session=owner)
    resolved_before_confirmation = linking.resolve.execute(telegram_user_id=OWNER_TG)
    linking.clock.advance(minutes=1)
    connection = linking.confirm.execute(challenge_id=issued.challenge_id, session=owner)
    connected = linking.status.execute(challenge_id=issued.challenge_id, session=owner)

    assert issued.deep_link.startswith("https://t.me/mintflow_test_bot?start=")
    assert waiting is not None and waiting.state is TelegramLinkState.WAITING_FOR_TELEGRAM
    assert claimed is True
    assert pending is not None and pending.state is TelegramLinkState.AWAITING_CONFIRMATION
    assert pending.telegram_display_name == "Ada (@ada)"
    assert resolved_before_confirmation is None  # a claim is not a connection
    assert connection is not None and connection.telegram_user_id == OWNER_TG
    assert connected is not None and connected.state is TelegramLinkState.CONNECTED
    resolved = linking.resolve.execute(telegram_user_id=OWNER_TG)
    assert resolved is not None and resolved.id == owner.user_id
    assert linking.connection.execute(user_id=owner.user_id) == connection


def test_a_claim_by_the_wrong_person_stays_pending_and_expires(db_session: Session) -> None:
    linking = Linking(db_session)
    owner = _account(db_session)
    issued = linking.issue.execute(session=owner)

    # Someone who intercepted the deep link claims it first.
    assert linking.claim.execute(
        payload=linking.token_of(issued.deep_link),
        telegram_user_id=INTERCEPTOR_TG,
        display_name="Mallory",
    )
    owner_retry = linking.claim.execute(
        payload=linking.token_of(issued.deep_link),
        telegram_user_id=OWNER_TG,
        display_name="Ada",
    )
    shown = linking.status.execute(challenge_id=issued.challenge_id, session=owner)
    linking.clock.advance(minutes=5)
    expired = linking.status.execute(challenge_id=issued.challenge_id, session=owner)
    late_confirmation = linking.confirm.execute(challenge_id=issued.challenge_id, session=owner)

    assert owner_retry is False
    # The owner sees who claimed it and can simply not confirm.
    assert shown is not None and shown.telegram_display_name == "Mallory"
    assert expired is not None and expired.state is TelegramLinkState.EXPIRED
    assert late_confirmation is None
    assert linking.resolve.execute(telegram_user_id=INTERCEPTOR_TG) is None
    assert linking.connection.execute(user_id=owner.user_id) is None


def test_another_session_cannot_see_or_confirm_a_challenge(db_session: Session) -> None:
    linking = Linking(db_session)
    owner, stranger = _account(db_session), _account(db_session)
    issued = linking.issue.execute(session=owner)
    assert linking.claim.execute(
        payload=linking.token_of(issued.deep_link), telegram_user_id=OWNER_TG, display_name=None
    )

    assert linking.status.execute(challenge_id=issued.challenge_id, session=stranger) is None
    assert linking.confirm.execute(challenge_id=issued.challenge_id, session=stranger) is None
    assert linking.connection.execute(user_id=stranger.user_id) is None


def test_unlink_stops_resolution_immediately_and_allows_relinking(db_session: Session) -> None:
    linking = Linking(db_session)
    owner = _account(db_session)

    def link() -> None:
        issued = linking.issue.execute(session=owner)
        assert linking.claim.execute(
            payload=linking.token_of(issued.deep_link),
            telegram_user_id=OWNER_TG,
            display_name=None,
        )
        linking.clock.advance(seconds=1)
        assert linking.confirm.execute(challenge_id=issued.challenge_id, session=owner)

    link()
    linking.clock.advance(minutes=1)
    assert linking.unlink.execute(user_id=owner.user_id) is True
    assert linking.resolve.execute(telegram_user_id=OWNER_TG) is None
    assert linking.unlink.execute(user_id=owner.user_id) is False

    linking.clock.advance(minutes=1)
    link()
    resolved = linking.resolve.execute(telegram_user_id=OWNER_TG)
    assert resolved is not None and resolved.id == owner.user_id


def test_raw_tokens_are_never_persisted(db_session: Session) -> None:
    linking = Linking(db_session)
    owner = _account(db_session)
    issued = linking.issue.execute(session=owner)
    token = linking.token_of(issued.deep_link)
    linking.claim.execute(payload=token, telegram_user_id=OWNER_TG, display_name=None)

    dump = " ".join(
        db_session.execute(
            text(
                "SELECT row_to_json(c)::text FROM telegram_link_challenges c "
                "UNION ALL SELECT row_to_json(a)::text FROM authentication_audit_records a"
            )
        ).scalars()
    )
    assert token not in dump
