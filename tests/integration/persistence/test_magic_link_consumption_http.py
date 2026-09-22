import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mintflow.application.authentication import (
    ConsumeMagicLink,
    GeneratedToken,
    MagicLinkConsumptionResult,
    hash_token,
)
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import AUTHENTICATED_SESSION_COOKIE_NAME, AuthenticationRuntime
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"
TOKEN = "A" * 43


def _settings(database_url: str, *, with_email_sender: bool = True) -> Settings:
    return Settings(
        environment="test",
        log_level="CRITICAL",
        database_url=SecretStr(database_url),
        authentication_rate_limit_key=SecretStr("integration-rate-limit-key"),
        authentication_csrf_signing_key=SecretStr("integration-csrf-signing-key"),
        authentication_web_origin=ORIGIN,
        authentication_return_targets=frozenset({"dashboard"}),
        email_backend="mailpit" if with_email_sender else None,
        mailpit_smtp_host="localhost" if with_email_sender else None,
        mailpit_from_email="no-reply@mintflow.dev" if with_email_sender else None,
    )


def _application(database_url: str, *, with_email_sender: bool = True) -> FastAPI:
    application = create_app(_settings(database_url, with_email_sender=with_email_sender))
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


def _add_challenge(
    session: Session,
    *,
    token: str = TOKEN,
    email: str = "person@example.com",
    expires_at: datetime = NOW + timedelta(minutes=15),
) -> LoginChallengeRecord:
    challenge = LoginChallengeRecord(
        canonical_email=email,
        token_hash=hash_token(token),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=expires_at,
        return_target="dashboard",
    )
    session.add(challenge)
    session.commit()
    return challenge


async def _post(
    application: FastAPI, token: str = TOKEN, *, raise_exceptions: bool = True
) -> Response:
    transport = ASGITransport(app=application, raise_app_exceptions=raise_exceptions)
    async with AsyncClient(transport=transport, base_url=ORIGIN, follow_redirects=False) as client:
        return await client.post(
            "/auth/magic-link",
            headers={"Origin": ORIGIN},
            data={"token": token, "return_target": "https://attacker.test"},
        )


@pytest.mark.anyio
async def test_endpoint_first_and_returning_login_use_fresh_hash_only_sessions(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_challenge(db_session)
    application = _application(migrated_database_url)

    first = await _post(application)
    first_cookie = first.cookies[AUTHENTICATED_SESSION_COOKIE_NAME]
    _add_challenge(db_session, token="B" * 43)
    returning = await _post(application, "B" * 43)
    returning_cookie = returning.cookies[AUTHENTICATED_SESSION_COOKIE_NAME]

    assert first.status_code == returning.status_code == 303
    assert first.headers["location"] == returning.headers["location"] == "/dashboard"
    assert first_cookie != returning_cookie
    assert db_session.scalar(select(func.count()).select_from(UserRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(EmailIdentityRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 2
    hashes = set(db_session.scalars(select(WebSessionRecord.secret_hash)))
    assert hashes == {hash_token(first_cookie), hash_token(returning_cookie)}
    assert first_cookie.encode() not in hashes
    assert returning_cookie.encode() not in hashes
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_consumption_resolves_without_email_sender(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_challenge(db_session)
    application = _application(migrated_database_url, with_email_sender=False)

    response = await _post(application)

    assert application.state.authentication_email_sender is None
    assert response.status_code == 303
    assert AUTHENTICATED_SESSION_COOKIE_NAME in response.cookies
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 1
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_invalid_expired_reused_and_unknown_are_equivalent(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_challenge(db_session)
    _add_challenge(db_session, token="B" * 43, expires_at=NOW)
    application = _application(migrated_database_url)

    success = await _post(application)
    responses = [
        await _post(application),
        await _post(application, "B" * 43),
        await _post(application, "C" * 43),
    ]

    assert success.status_code == 303
    assert {(response.status_code, response.content) for response in responses} == {
        (400, responses[0].content)
    }
    assert all("set-cookie" not in response.headers for response in responses)
    expired = db_session.scalar(
        select(LoginChallengeRecord).where(LoginChallengeRecord.token_hash == hash_token("B" * 43))
    )
    assert expired is not None and expired.consumed_at is None
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_refuses_deactivated_user_and_preserves_other_sessions(
    db_session: Session, migrated_database_url: str
) -> None:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        EmailIdentityRecord(
            user_id=user.id,
            canonical_email="person@example.com",
            display_email="person@example.com",
            verified_at=NOW,
            created_at=NOW,
        )
    )
    db_session.add(
        WebSessionRecord(
            user_id=user.id,
            secret_hash=hash_token("other-session"),
            issued_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )
    )
    db_session.commit()
    _add_challenge(db_session)
    application = _application(migrated_database_url)

    success = await _post(application)

    assert success.status_code == 303
    sessions = list(db_session.scalars(select(WebSessionRecord)))
    assert len(sessions) == 2
    assert all(session.revoked_at is None for session in sessions)

    user.status = UserStatus.DEACTIVATED.value
    user.deactivated_at = NOW
    db_session.commit()
    _add_challenge(db_session, token="B" * 43)
    refused = await _post(application, "B" * 43)

    assert refused.status_code == 400
    assert "set-cookie" not in refused.headers
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 2
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_endpoint_persistence_failure_rolls_back_and_emits_no_cookie(
    db_session: Session,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_challenge(db_session)
    application = _application(migrated_database_url)
    duplicate = GeneratedToken(raw="duplicate-secret", digest=hash_token("duplicate-secret"))
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        WebSessionRecord(
            user_id=user.id,
            secret_hash=duplicate.digest,
            issued_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )
    )
    db_session.commit()
    monkeypatch.setattr("mintflow.http.authentication.generate_token", lambda: duplicate)

    response = await _post(application, raise_exceptions=False)

    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    challenge = db_session.scalar(select(LoginChallengeRecord))
    assert challenge is not None and challenge.consumed_at is None
    assert db_session.scalar(select(func.count()).select_from(EmailIdentityRecord)) == 0
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(AuthenticationAuditRecordModel)) == 0
    application.state.database_engine.dispose()


def test_concurrent_endpoint_posts_create_exactly_one_authenticated_result(
    db_session: Session,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_challenge(db_session)
    application = _application(migrated_database_url)
    barrier = Barrier(2)
    original_execute = ConsumeMagicLink.execute

    def synchronized_execute(self: ConsumeMagicLink, *, token: str) -> MagicLinkConsumptionResult:
        barrier.wait()
        return original_execute(self, token=token)

    monkeypatch.setattr(ConsumeMagicLink, "execute", synchronized_execute)

    def worker() -> Response:
        return asyncio.run(_post(application))

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: worker(), range(2)))

    assert sum(response.status_code == 303 for response in responses) == 1
    assert sum("set-cookie" in response.headers for response in responses) == 1
    assert db_session.scalar(select(func.count()).select_from(WebSessionRecord)) == 1
    audits = list(db_session.scalars(select(AuthenticationAuditRecordModel)))
    assert sum(record.event_type == "login_succeeded" for record in audits) == 1
    application.state.database_engine.dispose()
