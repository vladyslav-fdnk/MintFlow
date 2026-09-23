import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.telegram import TELEGRAM_LINK_CHALLENGE_LIFETIME
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    TelegramConnectionRecord,
    TelegramLinkChallengeRecord,
    UserRecord,
    WebSessionRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
ALICE_TG = 111_111
BOB_TG = 222_222


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _add_user(session: Session, *, status: UserStatus = UserStatus.ACTIVE) -> UUID:
    user = UserRecord(status=status.value, created_at=NOW - timedelta(days=1))
    session.add(user)
    session.commit()
    return user.id


def _add_web_session(
    session: Session, user_id: UUID, *, revoked: bool = False, expired: bool = False
) -> UUID:
    record = WebSessionRecord(
        user_id=user_id,
        secret_hash=_hash(str(uuid4())),
        issued_at=NOW - timedelta(days=2),
        expires_at=NOW - timedelta(minutes=1) if expired else NOW + timedelta(days=1),
        revoked_at=NOW - timedelta(hours=1) if revoked else None,
    )
    session.add(record)
    session.commit()
    return record.id


class Account:
    def __init__(self, session: Session, *, status: UserStatus = UserStatus.ACTIVE) -> None:
        self.user_id = _add_user(session, status=status)
        self.web_session_id = _add_web_session(session, self.user_id)


def _issue(
    repository: SqlAlchemyTelegramLinkRepository,
    account: Account,
    token: str,
    *,
    at: datetime = NOW,
) -> UUID:
    return repository.create_challenge(
        token_hash=_hash(token),
        user_id=account.user_id,
        web_session_id=account.web_session_id,
        issued_at=at,
        expires_at=at + TELEGRAM_LINK_CHALLENGE_LIFETIME,
    ).id


def _claim(
    repository: SqlAlchemyTelegramLinkRepository,
    token: str,
    telegram_user_id: int,
    *,
    at: datetime = NOW + timedelta(minutes=1),
) -> bool:
    return (
        repository.claim(
            token_hash=_hash(token),
            telegram_user_id=telegram_user_id,
            display_name="Ada (@ada)",
            now=at,
        )
        is not None
    )


def _audit_types(session: Session, user_id: UUID) -> list[str]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuthenticationAuditRecordModel.event_type)
            .where(AuthenticationAuditRecordModel.user_id == user_id)
            .order_by(AuthenticationAuditRecordModel.occurred_at)
        ).all()
    )


@pytest.fixture
def repository(db_session: Session) -> SqlAlchemyTelegramLinkRepository:
    return SqlAlchemyTelegramLinkRepository(db_session)


def test_full_ceremony_links_and_audits(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    challenge_id = _issue(repository, alice, "token-a")

    claimed = repository.claim(
        token_hash=_hash("token-a"),
        telegram_user_id=ALICE_TG,
        display_name="Ada (@ada)",
        now=NOW + timedelta(minutes=1),
    )
    status = repository.challenge_for_session(
        challenge_id=challenge_id, web_session_id=alice.web_session_id
    )
    connection = repository.confirm(
        challenge_id=challenge_id,
        web_session_id=alice.web_session_id,
        now=NOW + timedelta(minutes=2),
    )

    assert claimed is not None and claimed.claimed_telegram_user_id == ALICE_TG
    assert status is not None and status.claimed_telegram_display_name == "Ada (@ada)"
    assert connection is not None
    assert (connection.user_id, connection.telegram_user_id) == (alice.user_id, ALICE_TG)
    assert connection.telegram_display_name == "Ada (@ada)"
    assert repository.active_connection_for_telegram_user(telegram_user_id=ALICE_TG) == connection
    assert repository.active_connection_for_user(user_id=alice.user_id) == connection
    confirmed = repository.challenge_for_session(
        challenge_id=challenge_id, web_session_id=alice.web_session_id
    )
    assert confirmed is not None and confirmed.is_confirmed
    assert _audit_types(db_session, alice.user_id) == ["telegram_link_claimed", "telegram_linked"]


def test_challenge_status_is_visible_only_to_the_initiating_session(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    other_session_of_alice = _add_web_session(db_session, alice.user_id)
    challenge_id = _issue(repository, alice, "token-a")

    assert (
        repository.challenge_for_session(
            challenge_id=challenge_id, web_session_id=other_session_of_alice
        )
        is None
    )


def test_claim_fails_for_unknown_expired_reused_and_ineligible_challenges(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    deactivated = Account(db_session)
    _issue(repository, alice, "expires")
    _issue(repository, alice, "reused")
    _issue(repository, deactivated, "deactivated")
    db_session.execute(
        update(UserRecord)
        .where(UserRecord.id == deactivated.user_id)
        .values(status=UserStatus.DEACTIVATED.value, deactivated_at=NOW)
    )
    db_session.commit()

    assert not _claim(repository, "never-issued", ALICE_TG)
    assert not _claim(repository, "expires", ALICE_TG, at=NOW + TELEGRAM_LINK_CHALLENGE_LIFETIME)
    assert _claim(repository, "reused", ALICE_TG)
    assert not _claim(repository, "reused", ALICE_TG)
    assert not _claim(repository, "reused", BOB_TG)
    assert not _claim(repository, "deactivated", BOB_TG)


def test_claim_fails_when_the_telegram_user_is_already_connected_anywhere(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice, bob = Account(db_session), Account(db_session)
    alice_challenge = _issue(repository, alice, "alice")
    assert _claim(repository, "alice", ALICE_TG)
    assert repository.confirm(
        challenge_id=alice_challenge,
        web_session_id=alice.web_session_id,
        now=NOW + timedelta(minutes=2),
    )
    _issue(repository, bob, "bob", at=NOW + timedelta(minutes=3))

    assert not _claim(repository, "bob", ALICE_TG, at=NOW + timedelta(minutes=4))


@pytest.mark.parametrize(
    "case", ["other session", "revoked session", "expired session", "unclaimed", "expired"]
)
def test_confirm_fails_without_a_valid_initiating_session_and_claim(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository, case: str
) -> None:
    alice = Account(db_session)
    challenge_id = _issue(repository, alice, "token")
    if case != "unclaimed":
        assert _claim(repository, "token", ALICE_TG)
    web_session_id = alice.web_session_id
    confirm_at = NOW + timedelta(minutes=2)
    if case == "other session":
        web_session_id = _add_web_session(db_session, alice.user_id)
    elif case in ("revoked session", "expired session"):
        db_session.execute(
            update(WebSessionRecord)
            .where(WebSessionRecord.id == web_session_id)
            .values(
                revoked_at=NOW if case == "revoked session" else None,
                expires_at=NOW + timedelta(seconds=30)
                if case == "expired session"
                else WebSessionRecord.expires_at,
            )
        )
        db_session.commit()
    elif case == "expired":
        confirm_at = NOW + TELEGRAM_LINK_CHALLENGE_LIFETIME

    connection = repository.confirm(
        challenge_id=challenge_id, web_session_id=web_session_id, now=confirm_at
    )

    assert connection is None
    assert repository.active_connection_for_user(user_id=alice.user_id) is None


def test_confirm_conflict_rolls_back_connection_and_confirmation_together(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice, bob = Account(db_session), Account(db_session)
    alice_challenge = _issue(repository, alice, "alice")
    bob_challenge = _issue(repository, bob, "bob")
    # Both claims succeed because neither Telegram user is connected yet.
    assert _claim(repository, "alice", ALICE_TG)
    assert _claim(repository, "bob", ALICE_TG)
    assert repository.confirm(
        challenge_id=alice_challenge,
        web_session_id=alice.web_session_id,
        now=NOW + timedelta(minutes=2),
    )

    conflicting = repository.confirm(
        challenge_id=bob_challenge,
        web_session_id=bob.web_session_id,
        now=NOW + timedelta(minutes=2),
    )

    assert conflicting is None
    bob_view = repository.challenge_for_session(
        challenge_id=bob_challenge, web_session_id=bob.web_session_id
    )
    assert bob_view is not None and not bob_view.is_confirmed
    assert repository.active_connection_for_user(user_id=bob.user_id) is None
    active = repository.active_connection_for_telegram_user(telegram_user_id=ALICE_TG)
    assert active is not None and active.user_id == alice.user_id
    assert "telegram_linked" not in _audit_types(db_session, bob.user_id)


def test_a_user_with_an_active_connection_cannot_link_a_second_one(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    first = _issue(repository, alice, "first")
    assert _claim(repository, "first", ALICE_TG)
    assert repository.confirm(
        challenge_id=first, web_session_id=alice.web_session_id, now=NOW + timedelta(minutes=2)
    )
    second = _issue(repository, alice, "second", at=NOW + timedelta(minutes=3))
    assert _claim(repository, "second", BOB_TG, at=NOW + timedelta(minutes=4))

    assert (
        repository.confirm(
            challenge_id=second,
            web_session_id=alice.web_session_id,
            now=NOW + timedelta(minutes=5),
        )
        is None
    )


def test_unlink_is_immediate_idempotent_and_allows_relinking(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice, bob = Account(db_session), Account(db_session)
    first = _issue(repository, alice, "first")
    assert _claim(repository, "first", ALICE_TG)
    assert repository.confirm(
        challenge_id=first, web_session_id=alice.web_session_id, now=NOW + timedelta(minutes=2)
    )

    assert repository.unlink(user_id=alice.user_id, now=NOW + timedelta(minutes=3)) is True
    assert repository.unlink(user_id=alice.user_id, now=NOW + timedelta(minutes=4)) is False
    assert repository.unlink(user_id=bob.user_id, now=NOW + timedelta(minutes=4)) is False
    assert repository.active_connection_for_telegram_user(telegram_user_id=ALICE_TG) is None

    # The freed Telegram identity can now be linked by another account.
    relink = _issue(repository, bob, "relink", at=NOW + timedelta(minutes=5))
    assert _claim(repository, "relink", ALICE_TG, at=NOW + timedelta(minutes=6))
    relinked = repository.confirm(
        challenge_id=relink, web_session_id=bob.web_session_id, now=NOW + timedelta(minutes=7)
    )
    assert relinked is not None and relinked.user_id == bob.user_id
    assert _audit_types(db_session, alice.user_id)[-1] == "telegram_unlinked"
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(TelegramConnectionRecord)
            .where(TelegramConnectionRecord.telegram_user_id == ALICE_TG)
        )
        == 2
    )


def _race[T](migrated_database_url: str, *work: Callable[[Session], T]) -> list[T]:
    """Run each callable on its own connection, released together by a barrier."""
    engines = [create_database_engine(migrated_database_url) for _ in work]
    sessions = [create_session_factory(engine)() for engine in engines]
    barrier = Barrier(len(work))

    def run(index: int) -> T:
        barrier.wait(timeout=5)
        return work[index](sessions[index])

    try:
        with ThreadPoolExecutor(max_workers=len(work)) as executor:
            return list(executor.map(run, range(len(work))))
    finally:
        for session in sessions:
            session.close()
        for engine in engines:
            engine.dispose()


def test_concurrent_claims_have_exactly_one_winner(
    db_session: Session, migrated_database_url: str, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    _issue(repository, alice, "contested")

    results = _race(
        migrated_database_url,
        *[
            (
                lambda session, tg=tg: _claim(
                    SqlAlchemyTelegramLinkRepository(session), "contested", tg
                )
            )
            for tg in (ALICE_TG, BOB_TG, 333_333, 444_444)
        ],
    )

    assert sorted(results) == [False, False, False, True]


def test_concurrent_confirmations_create_exactly_one_connection(
    db_session: Session, migrated_database_url: str, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice, bob = Account(db_session), Account(db_session)
    alice_challenge = _issue(repository, alice, "alice")
    bob_challenge = _issue(repository, bob, "bob")
    assert _claim(repository, "alice", ALICE_TG)
    assert _claim(repository, "bob", ALICE_TG)
    confirm_at = NOW + timedelta(minutes=2)

    def confirm(challenge_id: UUID, web_session_id: UUID) -> Callable[[Session], bool]:
        return lambda session: (
            SqlAlchemyTelegramLinkRepository(session).confirm(
                challenge_id=challenge_id, web_session_id=web_session_id, now=confirm_at
            )
            is not None
        )

    results = _race(
        migrated_database_url,
        confirm(alice_challenge, alice.web_session_id),
        confirm(alice_challenge, alice.web_session_id),
        confirm(bob_challenge, bob.web_session_id),
    )

    assert sorted(results) == [False, False, True]
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(TelegramConnectionRecord)
            .where(TelegramConnectionRecord.telegram_user_id == ALICE_TG)
        )
        == 1
    )


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "UPDATE telegram_link_challenges SET claimed_at = issued_at",
            "ck_telegram_link_challenges_complete_claim",
        ),
        (
            "UPDATE telegram_link_challenges SET confirmed_at = issued_at",
            "ck_telegram_link_challenges_confirmation_needs_claim",
        ),
        (
            "UPDATE telegram_link_challenges SET expires_at = issued_at + INTERVAL '6 minutes'",
            "ck_telegram_link_challenges_max_lifetime",
        ),
        (
            "UPDATE telegram_link_challenges SET token_hash = '\\x00'::bytea",
            "ck_telegram_link_challenges_token_hash_length",
        ),
        (
            "UPDATE telegram_link_challenges SET claimed_telegram_display_name = 'x'",
            "ck_telegram_link_challenges_display_name_needs_claim",
        ),
    ],
)
def test_challenge_check_constraints(
    db_session: Session,
    repository: SqlAlchemyTelegramLinkRepository,
    statement: str,
    constraint: str,
) -> None:
    _issue(repository, Account(db_session), "token")

    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(statement))
    db_session.rollback()


def test_challenge_session_must_belong_to_the_same_user(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice, bob = Account(db_session), Account(db_session)

    with pytest.raises(IntegrityError, match="fk_telegram_link_challenges_session_user"):
        repository.create_challenge(
            token_hash=_hash("mixed"),
            user_id=alice.user_id,
            web_session_id=bob.web_session_id,
            issued_at=NOW,
            expires_at=NOW + TELEGRAM_LINK_CHALLENGE_LIFETIME,
        )
    db_session.rollback()


def test_deleting_a_session_removes_its_challenges(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    _issue(repository, alice, "token")

    db_session.execute(delete(WebSessionRecord).where(WebSessionRecord.id == alice.web_session_id))
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(TelegramLinkChallengeRecord)) == 0


def test_active_uniqueness_is_enforced_by_the_database(db_session: Session) -> None:
    alice, bob = Account(db_session), Account(db_session)
    db_session.add(
        TelegramConnectionRecord(user_id=alice.user_id, telegram_user_id=ALICE_TG, linked_at=NOW)
    )
    db_session.commit()

    for record in (
        TelegramConnectionRecord(user_id=bob.user_id, telegram_user_id=ALICE_TG, linked_at=NOW),
        TelegramConnectionRecord(user_id=alice.user_id, telegram_user_id=BOB_TG, linked_at=NOW),
    ):
        db_session.add(record)
        with pytest.raises(IntegrityError, match="uq_telegram_connections_active"):
            db_session.commit()
        db_session.rollback()

    db_session.add(
        TelegramConnectionRecord(
            user_id=bob.user_id,
            telegram_user_id=ALICE_TG,
            linked_at=NOW,
            unlinked_at=NOW - timedelta(minutes=1),
        )
    )
    with pytest.raises(IntegrityError, match="ck_telegram_connections_unlinked_after_linked"):
        db_session.commit()
    db_session.rollback()


def test_audit_records_hold_no_token_material(
    db_session: Session, repository: SqlAlchemyTelegramLinkRepository
) -> None:
    alice = Account(db_session)
    challenge_id = _issue(repository, alice, "secret-token")
    assert _claim(repository, "secret-token", ALICE_TG)
    repository.confirm(
        challenge_id=challenge_id,
        web_session_id=alice.web_session_id,
        now=NOW + timedelta(minutes=2),
    )

    rows = db_session.execute(
        text("SELECT row_to_json(a)::text FROM authentication_audit_records a")
    ).scalars()
    serialized = " ".join(rows)
    assert "secret-token" not in serialized
    assert _hash("secret-token").hex() not in serialized
    assert str(ALICE_TG) not in serialized
