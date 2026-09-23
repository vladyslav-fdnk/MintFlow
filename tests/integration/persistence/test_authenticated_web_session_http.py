import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication import AuthenticatedWebSession, hash_token
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    AuthenticatedPrincipalDependency,
    AuthenticationRuntime,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyWebSessionRepository,
    create_database_engine,
    create_session_factory,
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

    @application.get("/test/protected")
    async def protected(principal: AuthenticatedPrincipalDependency) -> dict[str, str]:
        return {
            "user_id": str(principal.user_id),
            "web_session_id": str(principal.web_session_id),
        }

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


async def _get(application: FastAPI, secret: str | None) -> Response:
    headers = {} if secret is None else {"Cookie": f"{AUTHENTICATED_SESSION_COOKIE_NAME}={secret}"}
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.get("/test/protected", headers=headers)


@pytest.mark.anyio
async def test_endpoint_authenticates_multiple_sessions_without_mutation(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    first = _add_session(db_session, user_id=user.id, secret="A" * 43)
    second = _add_session(db_session, user_id=user.id, secret="B" * 43)
    before = [(row.id, row.expires_at, row.revoked_at) for row in (first, second)]
    application = _application(migrated_database_url)

    responses = [await _get(application, secret) for secret in ("A" * 43, "B" * 43)]

    assert [response.status_code for response in responses] == [200, 200]
    assert {response.json()["user_id"] for response in responses} == {str(user.id)}
    assert {response.json()["web_session_id"] for response in responses} == {
        str(first.id),
        str(second.id),
    }
    db_session.expire_all()
    after_rows = list(db_session.scalars(select(WebSessionRecord).order_by(WebSessionRecord.id)))
    assert sorted(before) == sorted((row.id, row.expires_at, row.revoked_at) for row in after_rows)
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 2
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_rejects_all_invalid_session_states_uniformly_and_without_leakage(
    db_session: Session,
    migrated_database_url: str,
    app_logs: pytest.LogCaptureFixture,
) -> None:
    active_user = _add_user(db_session)
    inactive_user = _add_user(db_session, active=False)
    expired = _add_session(db_session, user_id=active_user.id, secret="C" * 43, expires_at=NOW)
    revoked = _add_session(
        db_session,
        user_id=active_user.id,
        secret="D" * 43,
        revoked_at=NOW - timedelta(seconds=1),
    )
    inactive = _add_session(db_session, user_id=inactive_user.id, secret="E" * 43)
    valid = _add_session(db_session, user_id=active_user.id, secret="F" * 43)
    application = _application(migrated_database_url)
    candidates = [
        None,
        "",
        "malformed",
        "G" * 43,
        "C" * 43,
        "D" * 43,
        "E" * 43,
        str(valid.id),
    ]

    with app_logs.at_level(logging.ERROR):
        responses = [await _get(application, candidate) for candidate in candidates]

    assert {(response.status_code, response.text) for response in responses} == {
        (401, '{"detail":"Authentication required."}')
    }
    captured = "\n".join(response.text for response in responses) + app_logs.text
    for secret in ("C" * 43, "D" * 43, "E" * 43, "F" * 43, "G" * 43):
        assert secret not in captured
        assert hash_token(secret).hex() not in captured
    assert expired.expires_at == NOW
    assert revoked.revoked_at is not None
    assert inactive.user_id == inactive_user.id
    application.state.database_engine.dispose()


@pytest.mark.parametrize("transition", ["revoke", "deactivate"])
def test_endpoint_authentication_serializes_with_security_transition(
    db_session: Session,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    user = _add_user(db_session)
    session_record = _add_session(db_session, user_id=user.id, secret="R" * 43)
    application = _application(migrated_database_url)
    authentication_started = Event()
    original_authenticate = SqlAlchemyWebSessionRepository.authenticate

    def synchronized_authenticate(
        self: SqlAlchemyWebSessionRepository, *, secret_hash: bytes, now: datetime
    ) -> AuthenticatedWebSession | None:
        authentication_started.set()
        return original_authenticate(self, secret_hash=secret_hash, now=now)

    monkeypatch.setattr(SqlAlchemyWebSessionRepository, "authenticate", synchronized_authenticate)
    transition_engine = create_database_engine(migrated_database_url)
    transition_session = create_session_factory(transition_engine)()
    transaction = transition_session.begin()
    if transition == "revoke":
        transition_session.execute(
            update(WebSessionRecord)
            .where(WebSessionRecord.id == session_record.id)
            .values(revoked_at=NOW)
        )
    else:
        transition_session.execute(
            update(UserRecord)
            .where(UserRecord.id == user.id)
            .values(status=UserStatus.DEACTIVATED.value, deactivated_at=NOW)
        )

    def request_worker() -> Response:
        return asyncio.run(_get(application, "R" * 43))

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            response_future = executor.submit(request_worker)
            assert authentication_started.wait(timeout=5)
            transaction.commit()
            response = response_future.result(timeout=5)
    finally:
        if transaction.is_active:
            transaction.rollback()
        transition_session.close()
        transition_engine.dispose()
        application.state.database_engine.dispose()

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
