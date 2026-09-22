import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mintflow.application.authentication import LogoutWebSession, hash_token
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticateWebSession,
    AuthenticationRuntime,
)
from mintflow.infrastructure.persistence import SqlAlchemyWebSessionRepository
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"


def _settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        log_level="CRITICAL",
        database_url=SecretStr(database_url),
        authentication_rate_limit_key=SecretStr("integration-rate-limit-key"),
        authentication_csrf_signing_key=SecretStr("integration-csrf-signing-key"),
        authentication_web_origin=ORIGIN,
        authentication_return_targets=frozenset({"dashboard"}),
        email_backend=None,
    )


def _application(database_url: str) -> FastAPI:
    application = create_app(_settings(database_url))
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    application.state.authentication_runtime = AuthenticationRuntime(
        session_factory=runtime.session_factory,
        email_sender=runtime.email_sender,
        link_builder=runtime.link_builder,
        rate_limit_digester=runtime.rate_limit_digester,
        csrf_digester=runtime.csrf_digester,
        clock=lambda: NOW,
    )
    return application


def _add_user(session: Session, *, active: bool = True) -> UserRecord:
    user = UserRecord(
        status=(UserStatus.ACTIVE if active else UserStatus.DEACTIVATED).value,
        created_at=NOW - timedelta(days=1),
        deactivated_at=None if active else NOW - timedelta(minutes=1),
    )
    session.add(user)
    session.commit()
    return user


def _add_session(
    session: Session,
    *,
    user_id: UUID,
    secret: str,
    expires_at: datetime = NOW + timedelta(days=1),
    revoked_at: datetime | None = None,
) -> WebSessionRecord:
    record = WebSessionRecord(
        user_id=user_id,
        secret_hash=hash_token(secret),
        issued_at=NOW - timedelta(days=1),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    session.add(record)
    session.commit()
    return record


async def _logout(
    application: FastAPI, *, secret: str, token: str | None = None, raise_exceptions: bool = True
) -> Response:
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    resolved_token = (
        token if token is not None else runtime.csrf_digester.derive(session_secret=secret)
    )
    transport = ASGITransport(app=application, raise_app_exceptions=raise_exceptions)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.post(
            "/auth/logout",
            cookies={AUTHENTICATED_SESSION_COOKIE_NAME: secret},
            headers={CSRF_HEADER_NAME: resolved_token, "Origin": ORIGIN},
        )


@pytest.mark.anyio
async def test_logout_revokes_current_session_preserves_others_and_records_audit(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    current = _add_session(db_session, user_id=user.id, secret="A" * 43)
    other = _add_session(db_session, user_id=user.id, secret="B" * 43)
    application = _application(migrated_database_url)

    response = await _logout(application, secret="A" * 43)

    assert response.status_code == 200
    db_session.expire_all()
    persisted_current = db_session.get(WebSessionRecord, current.id)
    assert persisted_current is not None
    assert persisted_current.revoked_at == NOW
    persisted_other = db_session.get(WebSessionRecord, other.id)
    assert persisted_other is not None
    assert persisted_other.revoked_at is None
    audit = db_session.scalar(
        select(AuthenticationAuditRecordModel).where(
            AuthenticationAuditRecordModel.subject_record_id == current.id
        )
    )
    assert audit is not None
    assert audit.event_type == "current_session_revoked"
    assert audit.outcome == "succeeded"
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_repeated_logout_is_idempotent_and_leaves_one_revocation(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    session = _add_session(db_session, user_id=user.id, secret="C" * 43)
    application = _application(migrated_database_url)

    first = await _logout(application, secret="C" * 43)
    second = await _logout(application, secret="C" * 43)

    assert first.status_code == second.status_code == 200
    db_session.expire_all()
    persisted = db_session.get(WebSessionRecord, session.id)
    assert persisted is not None
    assert persisted.revoked_at == NOW
    audits = list(
        db_session.scalars(
            select(AuthenticationAuditRecordModel).where(
                AuthenticationAuditRecordModel.subject_record_id == session.id
            )
        )
    )
    assert len(audits) == 2
    assert sum(record.outcome == "succeeded" for record in audits) == 1
    assert sum(record.outcome == "failed" for record in audits) == 1
    application.state.database_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", ["revoked", "expired", "deactivated", "unknown"])
async def test_logout_clears_the_cookie_without_disclosing_prior_invalidity(
    db_session: Session, migrated_database_url: str, scenario: str
) -> None:
    secret = "D" * 43
    if scenario != "unknown":
        user = _add_user(db_session, active=scenario != "deactivated")
        _add_session(
            db_session,
            user_id=user.id,
            secret=secret,
            expires_at=NOW - timedelta(seconds=1)
            if scenario == "expired"
            else NOW + timedelta(days=1),
            revoked_at=NOW - timedelta(seconds=1) if scenario == "revoked" else None,
        )
    application = _application(migrated_database_url)

    response = await _logout(application, secret=secret)

    assert response.status_code == 200
    assert response.json() == {"message": "Signed out."}
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{AUTHENTICATED_SESSION_COOKIE_NAME}=")
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_logout_with_invalid_csrf_leaves_the_session_active(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    session = _add_session(db_session, user_id=user.id, secret="E" * 43)
    application = _application(migrated_database_url)

    response = await _logout(
        application, secret="E" * 43, token="wrong-token", raise_exceptions=False
    )

    assert response.status_code == 403
    db_session.expire_all()
    persisted = db_session.get(WebSessionRecord, session.id)
    assert persisted is not None
    assert persisted.revoked_at is None
    still_authenticated = AuthenticateWebSession(
        repository=SqlAlchemyWebSessionRepository(db_session), clock=lambda: NOW
    ).execute(secret="E" * 43)
    assert still_authenticated is not None
    application.state.database_engine.dispose()


def test_concurrent_logout_requests_revoke_exactly_once_with_no_error(
    db_session: Session, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _add_user(db_session)
    session = _add_session(db_session, user_id=user.id, secret="F" * 43)
    application = _application(migrated_database_url)
    barrier = Barrier(2)
    original_execute = LogoutWebSession.execute

    def synchronized_execute(self: LogoutWebSession, *, secret: str) -> None:
        barrier.wait()
        original_execute(self, secret=secret)

    monkeypatch.setattr(LogoutWebSession, "execute", synchronized_execute)

    def worker() -> Response:
        return asyncio.run(_logout(application, secret="F" * 43))

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: worker(), range(2)))

    assert all(response.status_code == 200 for response in responses)
    db_session.expire_all()
    persisted = db_session.get(WebSessionRecord, session.id)
    assert persisted is not None
    assert persisted.revoked_at is not None
    audits = list(
        db_session.scalars(
            select(AuthenticationAuditRecordModel).where(
                AuthenticationAuditRecordModel.subject_record_id == session.id
            )
        )
    )
    assert len(audits) == 2
    assert sum(record.outcome == "succeeded" for record in audits) == 1
    assert sum(record.outcome == "failed" for record in audits) == 1
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 1
    application.state.database_engine.dispose()
