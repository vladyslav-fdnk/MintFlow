from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, event, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    MerchantName,
    Money,
    Receipt,
    ReceiptState,
    RecognitionResult,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyReceiptImageStore,
    SqlAlchemyReceiptRepository,
)
from mintflow.infrastructure.persistence.models import (
    MAX_RECEIPT_IMAGE_BYTES,
    ReceiptRecord,
    RecognitionResultRecord,
    UserRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=2)


def _user(session: Session) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=1))
    session.add(user)
    session.commit()
    return user.id


def _receipt(session: Session, owner_id: UUID, *, at: datetime = NOW) -> Receipt:
    receipt = Receipt.receive(owner_id=owner_id, now=at)
    SqlAlchemyReceiptRepository(session).create(receipt, telegram_file_id=f"file-{receipt.id}")
    session.commit()
    return receipt


@contextmanager
def _statements(engine: Engine) -> Iterator[list[str]]:
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_receipts_are_claimed_oldest_first_and_only_once(db_session: Session) -> None:
    owner_id = _user(db_session)
    newer = _receipt(db_session, owner_id, at=NOW)
    older = _receipt(db_session, owner_id, at=NOW - timedelta(minutes=1))
    repository = SqlAlchemyReceiptRepository(db_session)

    first = repository.claim_next(now=NOW, lease=LEASE)
    second = repository.claim_next(now=NOW, lease=LEASE)
    third = repository.claim_next(now=NOW, lease=LEASE)

    assert first is not None and first.id == older.id
    assert second is not None and second.id == newer.id
    assert third is None
    stored = repository.get(receipt_id=older.id)
    assert stored is not None and stored.state is ReceiptState.PROCESSING
    assert stored.attempt_id == first.attempt_id
    assert repository.telegram_file_id(receipt_id=older.id) == f"file-{older.id}"


def test_an_expired_lease_is_reclaimed_and_the_stale_attempt_cannot_finish(
    db_session: Session,
) -> None:
    owner_id = _user(db_session)
    _receipt(db_session, owner_id)
    repository = SqlAlchemyReceiptRepository(db_session)
    first = repository.claim_next(now=NOW, lease=LEASE)
    assert first is not None and first.attempt_id is not None

    assert repository.claim_next(now=NOW + LEASE - timedelta(seconds=1), lease=LEASE) is None
    second = repository.claim_next(now=NOW + LEASE, lease=LEASE)
    assert second is not None and second.attempt_id is not None

    stale = repository.save_finished(first.complete(attempt_id=first.attempt_id, now=NOW + LEASE))
    current = repository.save_finished(second.fail(attempt_id=second.attempt_id, now=NOW + LEASE))
    db_session.commit()

    assert (stale, current) == (False, True)
    stored = repository.get(receipt_id=first.id)
    assert stored is not None and stored.state is ReceiptState.RECOGNITION_FAILED
    assert repository.claim_next(now=NOW + timedelta(days=1), lease=LEASE) is None


def test_receipts_with_a_removed_image_are_never_claimed(db_session: Session) -> None:
    owner_id = _user(db_session)
    receipt = _receipt(db_session, owner_id)
    db_session.execute(
        update(ReceiptRecord).where(ReceiptRecord.id == receipt.id).values(image_removed_at=NOW)
    )
    db_session.commit()

    assert SqlAlchemyReceiptRepository(db_session).claim_next(now=NOW, lease=LEASE) is None


def test_concurrent_workers_never_claim_the_same_receipt(engine: Engine) -> None:
    with Session(engine) as setup:
        owner_id = _user(setup)
        for _ in range(6):
            _receipt(setup, owner_id)
    barrier = Barrier(3)

    def worker() -> list[UUID]:
        claimed: list[UUID] = []
        with Session(engine) as session:
            repository = SqlAlchemyReceiptRepository(session)
            barrier.wait(timeout=5)
            while (receipt := repository.claim_next(now=NOW, lease=LEASE)) is not None:
                claimed.append(receipt.id)
        return claimed

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: worker(), range(3)))

    all_claimed = [receipt_id for claimed in results for receipt_id in claimed]
    assert len(all_claimed) == len(set(all_claimed)) == 6


def test_results_round_trip_and_are_unique_per_attempt(db_session: Session) -> None:
    owner_id = _user(db_session)
    _receipt(db_session, owner_id)
    repository = SqlAlchemyReceiptRepository(db_session)
    claimed = repository.claim_next(now=NOW, lease=LEASE)
    assert claimed is not None and claimed.attempt_id is not None
    result = RecognitionResult.for_attempt(
        receipt=claimed,
        attempt_id=claimed.attempt_id,
        merchant=MerchantName("Corner Shop"),
        transaction_date=TransactionDate(date(2026, 9, 20)),
        total=Money(minor_units=1250, currency=CurrencyCode("EUR")),
        now=NOW,
    )

    repository.save_result(result)
    db_session.commit()

    assert repository.get_result(result_id=result.id) == result
    duplicate = RecognitionResult.for_attempt(
        receipt=claimed,
        attempt_id=claimed.attempt_id,
        merchant=None,
        transaction_date=None,
        total=None,
        now=NOW,
    )
    with pytest.raises(IntegrityError, match="uq_recognition_results_attempt"):
        repository.save_result(duplicate)
    db_session.rollback()


def test_images_are_stored_and_bounded(db_session: Session) -> None:
    owner_id = _user(db_session)
    receipt = _receipt(db_session, owner_id)
    store = SqlAlchemyReceiptImageStore(db_session)

    store.put(receipt_id=receipt.id, media_type="image/jpeg", content=b"\xff\xd8jpeg", now=NOW)
    db_session.commit()

    stored = store.get(receipt_id=receipt.id)
    assert stored is not None and (stored.media_type, stored.content) == (
        "image/jpeg",
        b"\xff\xd8jpeg",
    )
    assert store.get(receipt_id=uuid4()) is None


@pytest.mark.parametrize(
    ("media_type", "size", "constraint"),
    [
        ("image/jpeg", 0, "ck_receipt_images_size"),
        ("image/jpeg", MAX_RECEIPT_IMAGE_BYTES + 1, "ck_receipt_images_size"),
        ("application/pdf", 10, "ck_receipt_images_media_type"),
    ],
)
def test_image_constraints(
    db_session: Session, media_type: str, size: int, constraint: str
) -> None:
    receipt = _receipt(db_session, _user(db_session))

    with pytest.raises(IntegrityError, match=constraint):
        SqlAlchemyReceiptImageStore(db_session).put(
            receipt_id=receipt.id, media_type=media_type, content=b"x" * size, now=NOW
        )
    db_session.rollback()


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"state": "recognized"}, "ck_receipts_attempt_after_queue"),
        ({"state": "processing", "attempt_id": uuid4()}, "ck_receipts_lease_while_processing"),
        ({"state": "lost", "attempt_id": uuid4()}, "ck_receipts_state"),
    ],
)
def test_receipt_state_constraints(
    db_session: Session, values: dict[str, object], constraint: str
) -> None:
    receipt = _receipt(db_session, _user(db_session))

    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(
            update(ReceiptRecord).where(ReceiptRecord.id == receipt.id).values(**values)
        )
    db_session.rollback()


def test_a_receipt_draft_round_trips_its_links(db_session: Session) -> None:
    owner_id = _user(db_session)
    _receipt(db_session, owner_id)
    receipts = SqlAlchemyReceiptRepository(db_session)
    claimed = receipts.claim_next(now=NOW, lease=LEASE)
    assert claimed is not None and claimed.attempt_id is not None
    drafts = SqlAlchemyCaptureDraftRepository(db_session)
    draft = CaptureDraft.start_from_receipt(owner_id=owner_id, receipt_id=claimed.id, now=NOW)
    drafts.create(draft)
    result = RecognitionResult.for_attempt(
        receipt=claimed,
        attempt_id=claimed.attempt_id,
        merchant=MerchantName("Kiosk"),
        transaction_date=None,
        total=Money(minor_units=300, currency=CurrencyCode("EUR")),
        now=NOW,
    )
    receipts.save_result(result)
    applied = draft.apply_recognition(
        result=result, fallback_category_key=UNCATEGORIZED_KEY, now=NOW
    )
    drafts.update(applied)

    assert drafts.get(draft_id=draft.id, owner_id=owner_id) == applied

    # Result retention later deletes the result; the finished draft keeps its values.
    db_session.execute(
        delete(RecognitionResultRecord).where(RecognitionResultRecord.id == result.id)
    )
    db_session.commit()
    reloaded = drafts.get(draft_id=draft.id, owner_id=owner_id)
    assert reloaded is not None and reloaded.recognition_result_id is None
    assert reloaded.merchant == MerchantName("Kiosk")


def test_one_receipt_supports_one_draft_and_only_receipt_drafts_have_one(
    db_session: Session,
) -> None:
    owner_id = _user(db_session)
    receipt = _receipt(db_session, owner_id)
    drafts = SqlAlchemyCaptureDraftRepository(db_session)
    drafts.create(
        CaptureDraft.start_from_receipt(owner_id=owner_id, receipt_id=receipt.id, now=NOW)
    )

    with pytest.raises(IntegrityError, match="uq_capture_drafts_receipt_id"):
        drafts.create(
            CaptureDraft.start_from_receipt(owner_id=owner_id, receipt_id=receipt.id, now=NOW)
        )
    db_session.rollback()
    manual = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.TELEGRAM_MANUAL, now=NOW)
    drafts.create(manual)
    with pytest.raises(IntegrityError, match="ck_capture_drafts_receipt_matches_source"):
        db_session.execute(
            text("UPDATE capture_drafts SET receipt_id = :receipt WHERE id = :draft"),
            {"receipt": receipt.id, "draft": manual.id},
        )
    db_session.rollback()


def test_receipt_and_draft_reads_never_load_image_bytes(
    db_session: Session, engine: Engine
) -> None:
    owner_id = _user(db_session)
    receipt = _receipt(db_session, owner_id)
    SqlAlchemyReceiptImageStore(db_session).put(
        receipt_id=receipt.id, media_type="image/png", content=b"png", now=NOW
    )
    drafts = SqlAlchemyCaptureDraftRepository(db_session)
    draft = CaptureDraft.start_from_receipt(owner_id=owner_id, receipt_id=receipt.id, now=NOW)
    drafts.create(draft)
    db_session.expire_all()

    with _statements(engine) as seen:
        SqlAlchemyReceiptRepository(db_session).get(receipt_id=receipt.id)
        drafts.get(draft_id=draft.id, owner_id=owner_id)

    assert seen
    assert not any("receipt_images" in statement for statement in seen)
