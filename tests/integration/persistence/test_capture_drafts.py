from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.domain.capture import CaptureDraft, CaptureSource, CurrencyCode, Money
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    create_database_engine,
    create_session_factory,
)
from mintflow.infrastructure.persistence.models import CaptureDraftRecord, UserRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _add_user(session: Session) -> UserRecord:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    return user


def test_create_and_scoped_get_round_trip(db_session: Session) -> None:
    user = _add_user(db_session)
    other_owner = uuid4()
    draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.WEB_MANUAL, now=NOW)
    repository = SqlAlchemyCaptureDraftRepository(db_session)

    repository.create(draft)

    fetched = repository.get(draft_id=draft.id, owner_id=user.id)
    assert fetched == draft
    assert repository.get(draft_id=draft.id, owner_id=other_owner) is None


def test_update_advances_revision_and_persists_new_values(db_session: Session) -> None:
    user = _add_user(db_session)
    draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.WEB_MANUAL, now=NOW)
    repository = SqlAlchemyCaptureDraftRepository(db_session)
    repository.create(draft)

    updated = draft.set_amount(
        caller_id=user.id,
        amount=Money(minor_units=2500, currency=CurrencyCode("USD")),
        now=NOW + timedelta(minutes=1),
    )
    repository.update(updated)

    fetched = repository.get(draft_id=draft.id, owner_id=user.id)
    assert fetched == updated
    assert fetched is not None
    assert fetched.revision == 1


def test_confirmed_without_expense_id_violates_check_constraint(db_session: Session) -> None:
    user = _add_user(db_session)
    record = CaptureDraftRecord(
        id=uuid4(),
        owner_id=user.id,
        source=CaptureSource.WEB_MANUAL.value,
        state="confirmed",
        revision=0,
        expense_id=None,
        created_at=NOW,
        modified_at=NOW,
        confirmed_at=NOW,
    )
    db_session.add(record)

    with pytest.raises(IntegrityError, match="ck_capture_drafts_confirmed_requires_expense_id"):
        db_session.commit()
    db_session.rollback()


def test_locking_read_serializes_a_concurrent_confirmation_attempt(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(db_session).create(draft)

    engine_a = create_database_engine(migrated_database_url)
    engine_b = create_database_engine(migrated_database_url)
    session_a = create_session_factory(engine_a)()
    session_b = create_session_factory(engine_b)()
    repository_a = SqlAlchemyCaptureDraftRepository(session_a)
    repository_b = SqlAlchemyCaptureDraftRepository(session_b)
    locked = Event()
    release = Event()

    def worker_a() -> None:
        locked_draft = repository_a.get_for_update(draft_id=draft.id, owner_id=user.id)
        assert locked_draft is not None
        locked.set()
        assert release.wait(timeout=5)
        session_a.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(worker_a)
            assert locked.wait(timeout=5)
            future_b = executor.submit(
                repository_b.get_for_update, draft_id=draft.id, owner_id=user.id
            )
            with pytest.raises(TimeoutError):
                future_b.result(timeout=0.3)

            release.set()
            future_a.result(timeout=5)
            blocked_read = future_b.result(timeout=5)

        assert blocked_read is not None
        assert blocked_read.id == draft.id
    finally:
        session_b.rollback()
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()
