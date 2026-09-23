import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from mintflow.application.authentication.login_challenge import (
    EmailDeliveryError,
    MagicLinkMessage,
)
from mintflow.config import Settings
from mintflow.infrastructure.persistence.models import (
    AuthenticationAuditRecordModel,
    AuthenticationRateLimitBucketRecord,
    EmailIdentityRecord,
    LoginChallengeRecord,
    UserRecord,
)
from mintflow.main import create_app

pytestmark = pytest.mark.integration

GENERIC_BODY = {"message": "If the address can receive email, a sign-in link will arrive shortly."}


class RecordingEmailSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[MagicLinkMessage] = []

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        self.messages.append(message)
        if self.fail:
            raise EmailDeliveryError("expected provider failure")


def _application(
    *, settings: Settings, migrated_database_url: str, sender: RecordingEmailSender
) -> FastAPI:
    database_settings = settings.model_copy(
        update={"database_url": SecretStr(migrated_database_url)}
    )
    application = create_app(database_settings)
    application.state.authentication_runtime = replace(
        application.state.authentication_runtime,
        email_sender=sender,
    )
    return application


async def _request(
    application: FastAPI,
    *,
    email: str,
    network_source: str,
    return_target: str = "dashboard",
    headers: dict[str, str] | None = None,
) -> Response:
    transport = ASGITransport(app=application, client=(network_source, 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/auth/magic-link/request",
            json={"email": email, "return_target": return_target},
            headers=headers,
        )


def _public_response(
    response: Response,
) -> tuple[int, dict[str, object], tuple[tuple[str, str], ...]]:
    return response.status_code, response.json(), tuple(response.headers.items())


@pytest.mark.anyio
async def test_known_unknown_invalid_and_spoofed_requests_are_non_disclosing(
    settings: Settings,
    migrated_database_url: str,
    db_session: Session,
) -> None:
    known_user_id = uuid4()
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    db_session.add(UserRecord(id=known_user_id, status="active", created_at=now))
    db_session.flush()
    db_session.add(
        EmailIdentityRecord(
            user_id=known_user_id,
            canonical_email="known@example.com",
            display_email="known@example.com",
            verified_at=now,
            created_at=now,
        )
    )
    db_session.commit()
    sender = RecordingEmailSender()
    application = _application(
        settings=settings, migrated_database_url=migrated_database_url, sender=sender
    )

    responses = [
        await _request(application, email="known@example.com", network_source="192.0.2.10"),
        await _request(application, email="unseen@example.com", network_source="192.0.2.11"),
        await _request(application, email="not-an-email", network_source="192.0.2.12"),
        await _request(
            application,
            email="spoofed@example.com",
            network_source="192.0.2.13",
            headers={
                "Forwarded": "for=203.0.113.9",
                "X-Forwarded-For": "198.51.100.7",
            },
        ),
    ]

    assert all(
        _public_response(response) == _public_response(responses[0]) for response in responses
    )
    assert responses[0].json() == GENERIC_BODY
    assert len(sender.messages) == 3
    network_buckets = db_session.scalars(
        select(AuthenticationRateLimitBucketRecord).where(
            AuthenticationRateLimitBucketRecord.dimension == "network_request"
        )
    ).all()
    assert len(network_buckets) == 3


@pytest.mark.anyio
async def test_endpoint_enforces_exact_email_and_network_thresholds(
    settings: Settings,
    migrated_database_url: str,
    db_session: Session,
) -> None:
    sender = RecordingEmailSender()
    application = _application(
        settings=settings, migrated_database_url=migrated_database_url, sender=sender
    )

    email_responses = [
        await _request(
            application,
            email="email-limit@example.com",
            network_source=f"192.0.2.{index + 1}",
        )
        for index in range(4)
    ]
    network_responses = [
        await _request(
            application,
            email=f"network-limit-{index}@example.com",
            network_source="198.51.100.10",
        )
        for index in range(31)
    ]

    assert all(
        response.status_code == 200 and response.json() == GENERIC_BODY
        for response in email_responses
    )
    assert all(
        response.status_code == 200 and response.json() == GENERIC_BODY
        for response in network_responses
    )
    challenges = db_session.scalars(select(LoginChallengeRecord)).all()
    assert len(challenges) == 3 + 30
    assert len(sender.messages) == 3 + 30


@pytest.mark.anyio
async def test_provider_failure_consumes_email_capacity_and_emits_safe_evidence(
    settings: Settings,
    migrated_database_url: str,
    db_session: Session,
    app_logs: pytest.LogCaptureFixture,
) -> None:
    sender = RecordingEmailSender(fail=True)
    application = _application(
        settings=settings, migrated_database_url=migrated_database_url, sender=sender
    )
    raw_email = "provider-failure@example.com"
    raw_ip = "203.0.113.20"

    with app_logs.at_level(logging.DEBUG):
        responses = [
            await _request(
                application,
                email=raw_email,
                network_source=f"203.0.113.{20 + index}",
            )
            for index in range(4)
        ]

    assert all(
        response.status_code == 200 and response.json() == GENERIC_BODY for response in responses
    )
    assert len(sender.messages) == 3
    assert len(db_session.scalars(select(LoginChallengeRecord)).all()) == 3
    email_bucket = db_session.scalar(
        select(AuthenticationRateLimitBucketRecord).where(
            AuthenticationRateLimitBucketRecord.dimension == "email_delivery"
        )
    )
    assert email_bucket is not None
    assert email_bucket.count == 3
    audit_records = db_session.scalars(select(AuthenticationAuditRecordModel)).all()
    assert len(audit_records) == 4

    persisted_operational_values = " ".join(
        repr(value) for record in [email_bucket, *audit_records] for value in vars(record).values()
    )
    captured = app_logs.text
    forbidden_values = [
        raw_email,
        raw_ip,
        sender.messages[0].magic_link,
        sender.messages[0].magic_link.split("token=", 1)[1].split("&", 1)[0],
    ]
    for forbidden_value in forbidden_values:
        assert forbidden_value not in persisted_operational_values
        assert forbidden_value not in captured


@pytest.mark.anyio
@pytest.mark.parametrize(
    "return_target",
    [
        "https://attacker.example",
        "//attacker.example",
        "%2F%2Fattacker.example",
        "/dashboard",
        "dashboard?next=https://attacker.example",
    ],
)
async def test_unapproved_return_targets_are_safe_and_never_persisted(
    settings: Settings,
    migrated_database_url: str,
    db_session: Session,
    return_target: str,
) -> None:
    sender = RecordingEmailSender()
    application = _application(
        settings=settings, migrated_database_url=migrated_database_url, sender=sender
    )

    response = await _request(
        application,
        email="person@example.com",
        network_source="192.0.2.10",
        return_target=return_target,
    )

    assert response.status_code == 200
    assert response.json() == GENERIC_BODY
    assert db_session.scalar(select(LoginChallengeRecord)) is None
    audit_record = db_session.scalar(select(AuthenticationAuditRecordModel))
    assert audit_record is not None
    assert audit_record.outcome == "failed"
    assert audit_record.subject_record_id is None
    assert sender.messages == []
