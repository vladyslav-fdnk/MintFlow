"""Account deletion on PostgreSQL (docs/account_deletion_design.md, A1, A2, A5)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.authentication.audit import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
    AuthenticationAuditRecord,
)
from mintflow.application.capture import ConfirmCaptureDraft
from mintflow.application.capture.edit_expense import EditExpense, ExpenseEdit
from mintflow.application.users import DeleteAccount
from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Money,
    Receipt,
    RecognitionResult,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence import (
    PostgreSQLAccountDeletionRepository,
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseChangeRecordAppender,
    SqlAlchemyExpenseRepository,
    SqlAlchemyReceiptImageStore,
    SqlAlchemyReceiptRepository,
    SqlAlchemyUserRepository,
)
from mintflow.infrastructure.persistence import account_deletion as account_deletion_module
from mintflow.infrastructure.persistence.authentication_audit import (
    SqlAlchemyAuthenticationAuditAppender,
)
from mintflow.infrastructure.persistence.models import (
    EmailIdentityRecord,
    LoginChallengeRecord,
    TelegramConnectionRecord,
    TelegramConversationRecord,
    TelegramLinkChallengeRecord,
    UserRecord,
    WebSessionRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _seed_everything(session: Session, *, email: str, telegram_user_id: int) -> UUID:
    """A user with every kind of row MintFlow keeps: the ones deletion must remove."""
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - 30 * DAY)
    session.add(user)
    session.flush()
    session.add(
        EmailIdentityRecord(
            user_id=user.id,
            canonical_email=email,
            display_email=email,
            verified_at=NOW - 30 * DAY,
            created_at=NOW - 30 * DAY,
        )
    )
    session.add(
        LoginChallengeRecord(
            canonical_email=email,
            token_hash=hash_token(str(uuid4())),
            issued_at=NOW - DAY,
            expires_at=NOW - DAY + timedelta(minutes=15),
            consumed_at=NOW - DAY,
            return_target="dashboard",
        )
    )
    web_session = WebSessionRecord(
        user_id=user.id,
        secret_hash=hash_token(str(uuid4())),
        issued_at=NOW - DAY,
        expires_at=NOW + DAY,
    )
    session.add(web_session)
    session.flush()
    session.add(
        TelegramLinkChallengeRecord(
            token_hash=hash_token(str(uuid4())),
            initiating_user_id=user.id,
            initiating_web_session_id=web_session.id,
            issued_at=NOW - DAY,
            expires_at=NOW - DAY + timedelta(minutes=5),
        )
    )
    session.add(
        TelegramConnectionRecord(
            user_id=user.id, telegram_user_id=telegram_user_id, linked_at=NOW - DAY
        )
    )
    session.commit()

    # A confirmed receipt: receipt, image, recognition result, draft, expense, then an edit.
    receipts = SqlAlchemyReceiptRepository(session)
    drafts = SqlAlchemyCaptureDraftRepository(session)
    receipt = Receipt.receive(owner_id=user.id, now=NOW - DAY)
    attempt = receipt.claim(now=NOW - DAY, lease=timedelta(minutes=2))
    assert attempt.attempt_id is not None
    receipts.create(
        attempt.complete(attempt_id=attempt.attempt_id, now=NOW - DAY), telegram_file_id="file"
    )
    SqlAlchemyReceiptImageStore(session).put(
        receipt_id=receipt.id, media_type="image/jpeg", content=JPEG, now=NOW - DAY
    )
    result = RecognitionResult.for_attempt(
        receipt=receipt,
        attempt_id=attempt.attempt_id,
        merchant=None,
        transaction_date=TransactionDate((NOW - DAY).date()),
        total=Money(minor_units=1250, currency=CurrencyCode("EUR")),
        now=NOW - DAY,
    )
    receipts.save_result(result)
    receipt_draft = CaptureDraft.start_from_receipt(
        owner_id=user.id, receipt_id=receipt.id, now=NOW - DAY
    ).apply_recognition(result=result, fallback_category_key=UNCATEGORIZED_KEY, now=NOW - DAY)
    drafts.create(receipt_draft)
    session.commit()
    expense = ConfirmCaptureDraft(
        draft_repository=drafts,
        expense_repository=SqlAlchemyExpenseRepository(session),
        user_repository=SqlAlchemyUserRepository(session),
        clock=lambda: NOW - DAY,
    ).execute(draft_id=receipt_draft.id, caller_id=user.id)
    EditExpense(
        expense_repository=SqlAlchemyExpenseRepository(session),
        change_records=SqlAlchemyExpenseChangeRecordAppender(session),
        category_repository=SqlAlchemyCategoryRepository(session),
        user_repository=SqlAlchemyUserRepository(session),
        clock=lambda: NOW,
    ).execute(expense_id=expense.id, caller_id=user.id, edit=ExpenseEdit(note="lunch"))

    # An open manual draft the bot conversation points at.
    open_draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.TELEGRAM_MANUAL, now=NOW)
    drafts.create(open_draft)
    session.add(
        TelegramConversationRecord(
            user_id=user.id,
            active_draft_id=open_draft.id,
            awaiting="amount",
            currency_is_default=True,
            updated_at=NOW,
        )
    )
    session.commit()
    SqlAlchemyAuthenticationAuditAppender(session).append(
        AuthenticationAuditRecord(
            event_type=AuthenticationAuditEventType.LOGIN_SUCCEEDED,
            outcome=AuthenticationAuditOutcome.SUCCEEDED,
            occurred_at=NOW - DAY,
            user_id=user.id,
        )
    )
    return user.id


_REFERENCES = {
    "users": "SELECT count(*) FROM users WHERE id = :u",
    "email_identities": "SELECT count(*) FROM email_identities WHERE user_id = :u",
    "login_challenges": "SELECT count(*) FROM login_challenges WHERE canonical_email = :email",
    "web_sessions": "SELECT count(*) FROM web_sessions WHERE user_id = :u",
    "telegram_link_challenges": (
        "SELECT count(*) FROM telegram_link_challenges WHERE initiating_user_id = :u"
    ),
    "telegram_connections": "SELECT count(*) FROM telegram_connections WHERE user_id = :u",
    "telegram_conversations": "SELECT count(*) FROM telegram_conversations WHERE user_id = :u",
    "capture_drafts": "SELECT count(*) FROM capture_drafts WHERE owner_id = :u",
    "expenses": "SELECT count(*) FROM expenses WHERE owner_id = :u",
    "expense_change_records": (
        "SELECT count(*) FROM expense_change_records WHERE actor_user_id = :u"
    ),
    "receipts": "SELECT count(*) FROM receipts WHERE owner_id = :u",
    "receipt_images": (
        "SELECT count(*) FROM receipt_images WHERE receipt_id IN "
        "(SELECT id FROM receipts WHERE owner_id = :u)"
    ),
    "recognition_results": "SELECT count(*) FROM recognition_results WHERE owner_id = :u",
    "authentication_audit_records": (
        "SELECT count(*) FROM authentication_audit_records WHERE user_id = :u"
    ),
}


def _references(session: Session, user_id: UUID, email: str) -> dict[str, int]:
    session.expire_all()
    return {
        table: int(session.execute(text(query), {"u": user_id, "email": email}).scalar_one())
        for table, query in _REFERENCES.items()
    }


def _delete(session: Session, user_id: UUID, *, at: datetime = NOW) -> bool:
    return DeleteAccount(
        repository=PostgreSQLAccountDeletionRepository(session), clock=lambda: at
    ).execute(user_id=user_id)


def test_deletion_removes_every_row_of_the_user_and_nothing_of_another(
    db_session: Session,
) -> None:
    doomed = _seed_everything(db_session, email="doomed@example.com", telegram_user_id=111)
    kept = _seed_everything(db_session, email="kept@example.com", telegram_user_id=222)
    before = _references(db_session, doomed, "doomed@example.com")
    assert all(count > 0 for count in before.values()), before
    others_before = _references(db_session, kept, "kept@example.com")

    assert _delete(db_session, doomed) is True

    assert _references(db_session, doomed, "doomed@example.com") == dict.fromkeys(_REFERENCES, 0)
    assert _references(db_session, kept, "kept@example.com") == others_before


def test_the_audit_keeps_its_events_without_the_user_and_adds_an_anonymous_one(
    db_session: Session,
) -> None:
    doomed = _seed_everything(db_session, email="doomed@example.com", telegram_user_id=111)
    audit = "SELECT event_type, user_id FROM authentication_audit_records ORDER BY occurred_at"
    before = db_session.execute(text(audit)).all()

    _delete(db_session, doomed)

    after = db_session.execute(text(audit)).all()
    assert [event for event, _ in after] == [event for event, _ in before] + ["account_deleted"]
    assert all(user_id is None for _, user_id in after)


def test_a_tombstone_keeps_only_the_id_and_time(db_session: Session) -> None:
    doomed = _seed_everything(db_session, email="doomed@example.com", telegram_user_id=111)

    _delete(db_session, doomed)

    rows = db_session.execute(text("SELECT * FROM deleted_accounts")).mappings().all()
    assert [dict(row) for row in rows] == [{"user_id": doomed, "deleted_at": NOW}]


def test_deleting_again_is_a_quiet_no_op(db_session: Session) -> None:
    doomed = _seed_everything(db_session, email="doomed@example.com", telegram_user_id=111)

    assert _delete(db_session, doomed) is True
    assert _delete(db_session, doomed) is False
    assert _delete(db_session, uuid4()) is False


def test_a_failure_part_way_keeps_every_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = _seed_everything(db_session, email="doomed@example.com", telegram_user_id=111)
    before = _references(db_session, doomed, "doomed@example.com")
    statements = account_deletion_module._DELETE_OWNED_ROWS
    broken = (*statements[:5], "DELETE FROM no_such_table WHERE id = :user_id", *statements[5:])
    monkeypatch.setattr(account_deletion_module, "_DELETE_OWNED_ROWS", broken)

    with pytest.raises(ProgrammingError):
        _delete(db_session, doomed)

    db_session.rollback()
    assert _references(db_session, doomed, "doomed@example.com") == before
    assert db_session.execute(text("SELECT count(*) FROM deleted_accounts")).scalar_one() == 0


def test_concurrent_deletions_delete_once(db_session: Session, engine: Engine) -> None:
    doomed = _seed_everything(db_session, email="doomed@example.com", telegram_user_id=111)
    barrier = Barrier(2)

    def delete_in_own_session() -> bool:
        with Session(engine) as session:
            barrier.wait()
            return _delete(session, doomed)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: delete_in_own_session(), range(2)))

    assert outcomes == [False, True]
    assert _references(db_session, doomed, "doomed@example.com") == dict.fromkeys(_REFERENCES, 0)
    events = (
        "SELECT count(*) FROM authentication_audit_records WHERE event_type = 'account_deleted'"
    )
    assert db_session.execute(text(events)).scalar_one() == 1


def test_an_image_for_a_receipt_deleted_with_its_account_is_not_stored(
    db_session: Session,
) -> None:
    owner = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    db_session.add(owner)
    db_session.commit()
    receipt = Receipt.receive(owner_id=owner.id, now=NOW)
    SqlAlchemyReceiptRepository(db_session).create(receipt, telegram_file_id="file")
    db_session.commit()
    _delete(db_session, owner.id)

    stored = SqlAlchemyReceiptImageStore(db_session).put(
        receipt_id=receipt.id, media_type="image/jpeg", content=JPEG, now=NOW
    )

    assert stored is False
    db_session.rollback()
