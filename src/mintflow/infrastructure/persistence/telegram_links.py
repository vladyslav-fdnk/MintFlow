"""Telegram link ceremony persistence (authentication_persistence_design.md, 5.5-5.8).

Every state change is one transaction whose outcome is decided by a conditional
statement or a row lock plus the partial unique indexes, never by a pre-check
alone. Raw link tokens never reach this module; only their SHA-256 hashes do.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mintflow.application.authentication.audit import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
)
from mintflow.application.telegram.linking import TelegramConnection, TelegramLinkChallenge
from mintflow.domain.user import UserStatus
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    TelegramConnectionRecord,
    TelegramLinkChallengeRecord,
    UserRecord,
    WebSessionRecord,
)


def _challenge(record: TelegramLinkChallengeRecord) -> TelegramLinkChallenge:
    return TelegramLinkChallenge(
        id=record.id,
        initiating_user_id=record.initiating_user_id,
        initiating_web_session_id=record.initiating_web_session_id,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        claimed_at=record.claimed_at,
        claimed_telegram_user_id=record.claimed_telegram_user_id,
        claimed_telegram_display_name=record.claimed_telegram_display_name,
        confirmed_at=record.confirmed_at,
    )


def _connection(record: TelegramConnectionRecord) -> TelegramConnection:
    return TelegramConnection(
        id=record.id,
        user_id=record.user_id,
        telegram_user_id=record.telegram_user_id,
        telegram_display_name=record.telegram_display_name,
        linked_at=record.linked_at,
        unlinked_at=record.unlinked_at,
    )


_ACTIVE_USERS = select(UserRecord.id).where(UserRecord.status == UserStatus.ACTIVE.value)


class SqlAlchemyTelegramLinkRepository:
    """Writes commit on success and roll back on failure; reads join whatever transaction
    the shared request session already has open, so the repository composes with the other
    repositories on that session.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @contextmanager
    def _write(self) -> Iterator[None]:
        try:
            yield
            self._session.commit()
        except BaseException:
            self._session.rollback()
            raise

    def _audit(
        self,
        *,
        event_type: AuthenticationAuditEventType,
        user_id: UUID,
        subject_record_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._session.add(
            AuthenticationAuditRecordModel(
                occurred_at=occurred_at,
                event_type=event_type.value,
                outcome=AuthenticationAuditOutcome.SUCCEEDED.value,
                user_id=user_id,
                subject_record_id=subject_record_id,
            )
        )

    def create_challenge(
        self,
        *,
        token_hash: bytes,
        user_id: UUID,
        web_session_id: UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> TelegramLinkChallenge:
        record = TelegramLinkChallengeRecord(
            id=uuid4(),
            token_hash=token_hash,
            initiating_user_id=user_id,
            initiating_web_session_id=web_session_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        with self._write():
            self._session.add(record)
            self._session.flush()
            challenge = _challenge(record)
        return challenge

    def claim(
        self,
        *,
        token_hash: bytes,
        telegram_user_id: int,
        display_name: str | None,
        now: datetime,
    ) -> TelegramLinkChallenge | None:
        """Claim an unclaimed, unexpired challenge in one conditional update (design 5.5).

        Fails, without distinguishing why, when the challenge is unknown, expired,
        already claimed or confirmed, its User is not active, or the Telegram user
        already has an active connection anywhere. Creates no connection.
        """
        already_connected = exists().where(
            TelegramConnectionRecord.telegram_user_id == telegram_user_id,
            TelegramConnectionRecord.unlinked_at.is_(None),
        )
        with self._write():
            record = self._session.scalar(
                update(TelegramLinkChallengeRecord)
                .where(
                    TelegramLinkChallengeRecord.token_hash == token_hash,
                    TelegramLinkChallengeRecord.claimed_at.is_(None),
                    TelegramLinkChallengeRecord.confirmed_at.is_(None),
                    TelegramLinkChallengeRecord.expires_at > now,
                    TelegramLinkChallengeRecord.initiating_user_id.in_(_ACTIVE_USERS),
                    ~already_connected,
                )
                .values(
                    claimed_at=now,
                    claimed_telegram_user_id=telegram_user_id,
                    claimed_telegram_display_name=display_name,
                )
                .returning(TelegramLinkChallengeRecord)
            )
            if record is None:
                return None
            self._audit(
                event_type=AuthenticationAuditEventType.TELEGRAM_LINK_CLAIMED,
                user_id=record.initiating_user_id,
                subject_record_id=record.id,
                occurred_at=now,
            )
            return _challenge(record)

    def challenge_for_session(
        self, *, challenge_id: UUID, web_session_id: UUID
    ) -> TelegramLinkChallenge | None:
        """A challenge as seen by its initiating session only; None for anyone else."""
        record = self._session.scalar(
            select(TelegramLinkChallengeRecord).where(
                TelegramLinkChallengeRecord.id == challenge_id,
                TelegramLinkChallengeRecord.initiating_web_session_id == web_session_id,
            )
        )
        return _challenge(record) if record is not None else None

    def confirm(
        self, *, challenge_id: UUID, web_session_id: UUID, now: datetime
    ) -> TelegramConnection | None:
        """Activate the connection for a claimed challenge (design 5.6).

        The challenge row is locked; the initiating session must be the caller's,
        unrevoked, unexpired, and its User active. A conflict with either partial
        unique index rolls back both the connection and the confirmation.
        """
        with self._write():
            challenge = self._session.scalar(
                select(TelegramLinkChallengeRecord)
                .where(
                    TelegramLinkChallengeRecord.id == challenge_id,
                    TelegramLinkChallengeRecord.initiating_web_session_id == web_session_id,
                    TelegramLinkChallengeRecord.claimed_at.is_not(None),
                    TelegramLinkChallengeRecord.confirmed_at.is_(None),
                    TelegramLinkChallengeRecord.expires_at > now,
                )
                .with_for_update()
            )
            if challenge is None or challenge.claimed_telegram_user_id is None:
                return None
            session_is_valid = self._session.scalar(
                select(
                    exists().where(
                        WebSessionRecord.id == web_session_id,
                        WebSessionRecord.user_id == challenge.initiating_user_id,
                        WebSessionRecord.revoked_at.is_(None),
                        WebSessionRecord.expires_at > now,
                        WebSessionRecord.user_id.in_(_ACTIVE_USERS),
                    )
                )
            )
            if not session_is_valid:
                return None

            connection = TelegramConnectionRecord(
                id=uuid4(),
                user_id=challenge.initiating_user_id,
                telegram_user_id=challenge.claimed_telegram_user_id,
                telegram_display_name=challenge.claimed_telegram_display_name,
                linked_at=now,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(connection)
            except IntegrityError:
                # Either the User or the Telegram user is already connected: no transfer.
                return None
            challenge.confirmed_at = now
            self._audit(
                event_type=AuthenticationAuditEventType.TELEGRAM_LINKED,
                user_id=challenge.initiating_user_id,
                subject_record_id=connection.id,
                occurred_at=now,
            )
            return _connection(connection)

    def active_connection_for_user(self, *, user_id: UUID) -> TelegramConnection | None:
        record = self._session.scalar(
            select(TelegramConnectionRecord).where(
                TelegramConnectionRecord.user_id == user_id,
                TelegramConnectionRecord.unlinked_at.is_(None),
            )
        )
        return _connection(record) if record is not None else None

    def active_connection_for_telegram_user(
        self, *, telegram_user_id: int
    ) -> TelegramConnection | None:
        record = self._session.scalar(
            select(TelegramConnectionRecord).where(
                TelegramConnectionRecord.telegram_user_id == telegram_user_id,
                TelegramConnectionRecord.unlinked_at.is_(None),
            )
        )
        return _connection(record) if record is not None else None

    def unlink(self, *, user_id: UUID, now: datetime) -> bool:
        """Deactivate the User's active connection (design 5.8); False when there is none."""
        with self._write():
            connection_id = self._session.scalar(
                update(TelegramConnectionRecord)
                .where(
                    TelegramConnectionRecord.user_id == user_id,
                    TelegramConnectionRecord.unlinked_at.is_(None),
                )
                .values(unlinked_at=now)
                .returning(TelegramConnectionRecord.id)
            )
            if connection_id is None:
                return False
            self._audit(
                event_type=AuthenticationAuditEventType.TELEGRAM_UNLINKED,
                user_id=user_id,
                subject_record_id=connection_id,
                occurred_at=now,
            )
            return True
