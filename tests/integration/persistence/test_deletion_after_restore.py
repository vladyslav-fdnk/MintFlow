"""Deleted accounts stay deleted after a restore (docs/account_deletion_design.md, A5)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from mintflow.application.authentication.retention import CleanUpAuthenticationRetention
from mintflow.application.users import DeleteAccount
from mintflow.infrastructure.persistence import PostgreSQLAccountDeletionRepository
from mintflow.infrastructure.persistence.authentication_retention import (
    PostgreSQLAuthenticationRetentionRepository,
)

from .test_account_deletion import _REFERENCES, NOW, _references, _seed_everything

pytestmark = pytest.mark.integration

EMAIL = "restored@example.com"


def _cleanup(session: Session, *, at: datetime = NOW) -> tuple[int, int]:
    # Each retention step is its own transaction, as in the command's fresh session.
    session.commit()
    deletions = DeleteAccount(
        repository=PostgreSQLAccountDeletionRepository(session), clock=lambda: at
    )
    result = CleanUpAuthenticationRetention(
        repository=PostgreSQLAuthenticationRetentionRepository(session),
        delete_account=lambda user_id: deletions.execute(user_id=user_id),
        clock=lambda: at,
    ).execute(batch_size=100)
    return result.restored_accounts_deleted, result.deleted_account_tombstones_deleted


def _delete_then_restore(session: Session) -> UUID:
    """Delete an account, then bring its rows back under the same id, as a restore would."""
    user_id = _seed_everything(session, email=EMAIL, telegram_user_id=111)
    DeleteAccount(
        repository=PostgreSQLAccountDeletionRepository(session),
        clock=lambda: NOW - timedelta(days=2),
    ).execute(user_id=user_id)
    _seed_everything(session, email=EMAIL, telegram_user_id=111, user_id=user_id)
    return user_id


def _tombstones(session: Session) -> dict[UUID, datetime]:
    session.expire_all()
    rows = session.execute(text("SELECT user_id, deleted_at FROM deleted_accounts")).all()
    return {row.user_id: row.deleted_at for row in rows}


def test_one_cleanup_deletes_a_restored_account_again(db_session: Session) -> None:
    kept = _seed_everything(db_session, email="kept@example.com", telegram_user_id=222)
    kept_before = _references(db_session, kept, "kept@example.com")
    revived = _delete_then_restore(db_session)
    assert _references(db_session, revived, EMAIL)["expenses"] == 1

    assert _cleanup(db_session) == (1, 0)

    assert _references(db_session, revived, EMAIL) == dict.fromkeys(_REFERENCES, 0)
    assert _references(db_session, kept, "kept@example.com") == kept_before
    # The tombstone restarts its 35 days from this deletion.
    assert _tombstones(db_session) == {revived: NOW}
    assert _cleanup(db_session) == (0, 0)


def test_tombstones_older_than_35_days_are_pruned_and_younger_ones_stay(
    db_session: Session,
) -> None:
    at_cutoff, just_younger = uuid4(), uuid4()
    db_session.execute(
        text("INSERT INTO deleted_accounts (user_id, deleted_at) VALUES (:a, :at), (:b, :young)"),
        {
            "a": at_cutoff,
            "at": NOW - timedelta(days=35),
            "b": just_younger,
            "young": NOW - timedelta(days=35) + timedelta(seconds=1),
        },
    )
    db_session.commit()

    assert _cleanup(db_session) == (0, 1)

    assert set(_tombstones(db_session)) == {just_younger}


def test_an_old_tombstone_of_a_revived_account_deletes_it_before_going(
    db_session: Session,
) -> None:
    revived = _delete_then_restore(db_session)
    db_session.execute(
        text("UPDATE deleted_accounts SET deleted_at = :old"), {"old": NOW - timedelta(days=40)}
    )
    db_session.commit()

    assert _cleanup(db_session) == (1, 0)

    assert _references(db_session, revived, EMAIL)["users"] == 0
    assert _tombstones(db_session) == {revived: NOW}


def test_concurrent_cleanups_delete_a_restored_account_once(
    db_session: Session, engine: Engine
) -> None:
    revived = _delete_then_restore(db_session)
    barrier = Barrier(2)

    def run() -> tuple[int, int]:
        with Session(engine) as session:
            barrier.wait()
            return _cleanup(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(), range(2)))

    assert sorted(restored for restored, _ in outcomes) == [0, 1]
    assert _references(db_session, revived, EMAIL) == dict.fromkeys(_REFERENCES, 0)
    events = (
        "SELECT count(*) FROM authentication_audit_records WHERE event_type = 'account_deleted'"
    )
    # One from the original deletion, one from deleting the restored copy.
    assert db_session.execute(text(events)).scalar_one() == 2
