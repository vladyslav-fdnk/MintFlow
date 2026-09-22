from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from mintflow.application.authentication.audit import (
    AuthenticationAuditEventType,
    AuthenticationAuditOutcome,
    AuthenticationAuditRecord,
)
from mintflow.application.authentication.login_challenge import (
    INVALID_MAGIC_LINK_CONSUMPTION_RESULT,
    AuthenticatedUserIdentity,
    LoginChallenge,
    MagicLinkConsumptionResult,
)
from mintflow.application.authentication.web_session import WebSession
from mintflow.domain.user import User, UserStatus
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
    WebSessionRecord,
)


class SqlAlchemyLoginChallengeStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, challenge: LoginChallenge) -> None:
        self._session.add(
            LoginChallengeRecord(
                id=challenge.id,
                canonical_email=challenge.canonical_email,
                token_hash=challenge.token_hash,
                issued_at=challenge.issued_at,
                expires_at=challenge.expires_at,
                consumed_at=challenge.consumed_at,
                return_target=challenge.return_target,
            )
        )
        self._session.commit()


class SqlAlchemyLoginChallengeConsumer:
    def __init__(self, session: Session) -> None:
        self._session = session

    def consume(
        self,
        *,
        token_hash: bytes,
        consumed_at: datetime,
        session_factory: Callable[[UUID], WebSession],
    ) -> MagicLinkConsumptionResult:
        with self._session.begin():
            challenge = self._session.execute(
                update(LoginChallengeRecord)
                .where(
                    LoginChallengeRecord.token_hash == token_hash,
                    LoginChallengeRecord.consumed_at.is_(None),
                    LoginChallengeRecord.expires_at > consumed_at,
                )
                .values(consumed_at=consumed_at)
                .returning(
                    LoginChallengeRecord.canonical_email,
                    LoginChallengeRecord.return_target,
                )
            ).one_or_none()
            if challenge is None:
                self._session.add(
                    AuthenticationAuditRecordModel(
                        occurred_at=consumed_at,
                        event_type=AuthenticationAuditEventType.LOGIN_FAILED.value,
                        outcome=AuthenticationAuditOutcome.FAILED.value,
                    )
                )
                return INVALID_MAGIC_LINK_CONSUMPTION_RESULT

            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:canonical_email, 0))"),
                {"canonical_email": challenge.canonical_email},
            )
            identity_and_user = self._session.execute(
                select(EmailIdentityRecord, UserRecord)
                .join(UserRecord, EmailIdentityRecord.user_id == UserRecord.id)
                .where(EmailIdentityRecord.canonical_email == challenge.canonical_email)
                .with_for_update(of=UserRecord)
            ).one_or_none()

            if identity_and_user is None:
                created_user = User.create(now=consumed_at)
                user = UserRecord(
                    id=created_user.id,
                    status=created_user.status.value,
                    created_at=created_user.created_at,
                    deactivated_at=created_user.deactivated_at,
                )
                self._session.add(user)
                self._session.flush()
                self._session.add(
                    EmailIdentityRecord(
                        user_id=user.id,
                        canonical_email=challenge.canonical_email,
                        display_email=challenge.canonical_email,
                        verified_at=consumed_at,
                        created_at=consumed_at,
                    )
                )
            else:
                _, user = identity_and_user
                if user.status != UserStatus.ACTIVE.value:
                    self._session.add(
                        AuthenticationAuditRecordModel(
                            occurred_at=consumed_at,
                            event_type=AuthenticationAuditEventType.LOGIN_FAILED.value,
                            outcome=AuthenticationAuditOutcome.FAILED.value,
                            user_id=user.id,
                        )
                    )
                    return INVALID_MAGIC_LINK_CONSUMPTION_RESULT

            web_session = session_factory(user.id)
            self._session.add(
                WebSessionRecord(
                    id=web_session.id,
                    user_id=web_session.user_id,
                    secret_hash=web_session.secret_hash,
                    issued_at=web_session.issued_at,
                    expires_at=web_session.expires_at,
                    revoked_at=web_session.revoked_at,
                )
            )
            audit_record = AuthenticationAuditRecord(
                occurred_at=consumed_at,
                event_type=AuthenticationAuditEventType.LOGIN_SUCCEEDED,
                outcome=AuthenticationAuditOutcome.SUCCEEDED,
                user_id=user.id,
                subject_record_id=web_session.id,
            )
            self._session.add(
                AuthenticationAuditRecordModel(
                    id=audit_record.id,
                    occurred_at=audit_record.occurred_at,
                    event_type=audit_record.event_type.value,
                    outcome=audit_record.outcome.value,
                    user_id=audit_record.user_id,
                    subject_record_id=audit_record.subject_record_id,
                )
            )
            return MagicLinkConsumptionResult(
                identity=AuthenticatedUserIdentity(user_id=user.id),
                return_target=challenge.return_target,
            )
