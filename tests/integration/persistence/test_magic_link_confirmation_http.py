from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mintflow.application.authentication.tokens import hash_token
from mintflow.config import Settings
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    AuthenticationRateLimitBucketRecord,
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TOKEN = "P" * 43
PATH = f"/auth/magic-link?token={TOKEN}&return_target=dashboard"


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_head_and_scanner_repeats_leave_postgresql_unchanged(
    settings: Settings,
    migrated_database_url: str,
    db_session: Session,
) -> None:
    challenge = LoginChallengeRecord(
        canonical_email="person@example.com",
        token_hash=hash_token(TOKEN),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        consumed_at=None,
        return_target="dashboard",
    )
    db_session.add(challenge)
    db_session.commit()
    before = _authentication_state(db_session)

    application = create_app(
        settings.model_copy(update={"database_url": SecretStr(migrated_database_url)})
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for method in ("GET", "HEAD", "GET", "GET"):
            response = await client.request(method, PATH)
            assert response.status_code == 200
            assert "set-cookie" not in response.headers

    db_session.expire_all()
    assert _authentication_state(db_session) == before
    persisted_challenge = db_session.get(LoginChallengeRecord, challenge.id)
    assert persisted_challenge is not None
    assert persisted_challenge.consumed_at is None


def _authentication_state(session: Session) -> tuple[int, int, int, int, int, int]:
    record_types = (
        LoginChallengeRecord,
        UserRecord,
        EmailIdentityRecord,
        WebSessionRecord,
        AuthenticationAuditRecordModel,
        AuthenticationRateLimitBucketRecord,
    )
    return cast(
        tuple[int, int, int, int, int, int],
        tuple(
            session.scalar(select(func.count()).select_from(record_type)) or 0
            for record_type in record_types
        ),
    )
