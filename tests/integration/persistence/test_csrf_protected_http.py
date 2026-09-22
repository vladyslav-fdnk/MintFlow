from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
    CsrfProtectedPrincipalDependency,
)
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord
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

    @application.post("/test/csrf-protected")
    async def protected(principal: CsrfProtectedPrincipalDependency) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

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


async def _post(application: FastAPI, *, secret: str | None, token: str | None) -> Response:
    headers = {} if token is None else {CSRF_HEADER_NAME: token, "Origin": ORIGIN}
    cookies = {} if secret is None else {AUTHENTICATED_SESSION_COOKIE_NAME: secret}
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.post("/test/csrf-protected", headers=headers, cookies=cookies)


@pytest.mark.anyio
async def test_endpoint_accepts_a_persisted_session_with_its_derived_token(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "A" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token = runtime.csrf_digester.derive(session_secret=secret)

    response = await _post(application, secret=secret, token=token)

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user.id)}
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_rejects_token_for_a_revoked_session(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "B" * 43
    _add_session(db_session, user_id=user.id, secret=secret, revoked_at=NOW - timedelta(seconds=1))
    application = _application(migrated_database_url)
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token = runtime.csrf_digester.derive(session_secret=secret)

    response = await _post(application, secret=secret, token=token)

    assert response.status_code == 401
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_rejects_token_for_an_expired_session(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "C" * 43
    _add_session(db_session, user_id=user.id, secret=secret, expires_at=NOW)
    application = _application(migrated_database_url)
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token = runtime.csrf_digester.derive(session_secret=secret)

    response = await _post(application, secret=secret, token=token)

    assert response.status_code == 401
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_rejects_token_for_a_deactivated_users_session(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session, active=False)
    secret = "D" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token = runtime.csrf_digester.derive(session_secret=secret)

    response = await _post(application, secret=secret, token=token)

    assert response.status_code == 401
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_persisted_session_still_rejects_a_cross_session_token(
    db_session: Session, migrated_database_url: str
) -> None:
    first_user = _add_user(db_session)
    second_user = _add_user(db_session)
    first_secret = "E" * 43
    second_secret = "F" * 43
    _add_session(db_session, user_id=first_user.id, secret=first_secret)
    _add_session(db_session, user_id=second_user.id, secret=second_secret)
    application = _application(migrated_database_url)
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token_for_second_session = runtime.csrf_digester.derive(session_secret=second_secret)

    response = await _post(application, secret=first_secret, token=token_for_second_session)

    assert response.status_code == 403
    application.state.database_engine.dispose()
