from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from mintflow.domain.user import UserStatus


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(status = 'active' AND deactivated_at IS NULL) OR "
            "(status = 'deactivated' AND deactivated_at IS NOT NULL)",
            name="ck_users_status_deactivated_at",
        ),
        CheckConstraint(
            "deactivated_at IS NULL OR deactivated_at >= created_at",
            name="ck_users_deactivated_at_not_before_created_at",
        ),
        CheckConstraint(
            "status IN ('active', 'deactivated')",
            name="ck_users_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UserStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    email_identity: Mapped["EmailIdentityRecord | None"] = relationship(
        back_populates="user", uselist=False
    )


class EmailIdentityRecord(Base):
    __tablename__ = "email_identities"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_email_identities_user_id"),
        UniqueConstraint("canonical_email", name="uq_email_identities_canonical_email"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    canonical_email: Mapped[str] = mapped_column(Text, nullable=False)
    display_email: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[UserRecord] = relationship(back_populates="email_identity")


class LoginChallengeRecord(Base):
    __tablename__ = "login_challenges"
    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="ck_login_challenges_expiry"),
        CheckConstraint(
            "octet_length(token_hash) = 32", name="ck_login_challenges_token_hash_length"
        ),
        UniqueConstraint("token_hash", name="uq_login_challenges_token_hash"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    canonical_email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_target: Mapped[str] = mapped_column(Text, nullable=False)


class AuthenticationRateLimitBucketRecord(Base):
    __tablename__ = "authentication_rate_limit_buckets"
    __table_args__ = (
        CheckConstraint(
            "dimension IN ('email_delivery', 'network_request')",
            name="ck_auth_rate_limit_buckets_dimension",
        ),
        CheckConstraint(
            "octet_length(key_digest) = 32", name="ck_auth_rate_limit_key_digest_length"
        ),
        CheckConstraint("count > 0", name="ck_auth_rate_limit_buckets_count_positive"),
        CheckConstraint(
            "expires_at > window_started_at AND "
            "expires_at <= window_started_at + INTERVAL '24 hours'",
            name="ck_auth_rate_limit_buckets_expiry",
        ),
        Index("ix_auth_rate_limit_buckets_expires_at", "expires_at"),
    )

    dimension: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebSessionRecord(Base):
    __tablename__ = "web_sessions"
    __table_args__ = (
        CheckConstraint(
            "octet_length(secret_hash) = 32", name="ck_web_sessions_secret_hash_length"
        ),
        CheckConstraint("expires_at > issued_at", name="ck_web_sessions_expiry"),
        CheckConstraint(
            "expires_at <= issued_at + INTERVAL '30 days'",
            name="ck_web_sessions_max_lifetime",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_web_sessions_revoked_at_not_before_issued_at",
        ),
        UniqueConstraint("secret_hash", name="uq_web_sessions_secret_hash"),
        Index("ix_web_sessions_expires_at", "expires_at"),
        Index("ix_web_sessions_revoked_at", "revoked_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthenticationAuditRecordModel(Base):
    __tablename__ = "authentication_audit_records"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('login_challenge_requested', 'login_succeeded', 'login_failed', "
            "'current_session_revoked', 'all_sessions_revoked')",
            name="ck_auth_audit_records_event_type",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed')",
            name="ck_auth_audit_records_outcome",
        ),
        Index("ix_auth_audit_records_occurred_at", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    subject_record_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
