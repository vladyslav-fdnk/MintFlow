from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import quote_plus, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from mintflow.application.authentication.login_challenge import MagicLinkMessage
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
)
from mintflow.infrastructure.persistence.models import EmailIdentityRecord, UserRecord
from mintflow.main import create_app
from mintflow.web.testing import parse_html

pytestmark = pytest.mark.integration

ORIGIN = "https://app.mintflow.test"
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Origin": ORIGIN}


class RecordingEmailSender:
    def __init__(self) -> None:
        self.messages: list[MagicLinkMessage] = []

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        self.messages.append(message)


def _application(settings: Settings, database_url: str, sender: RecordingEmailSender) -> FastAPI:
    application = create_app(settings.model_copy(update={"database_url": SecretStr(database_url)}))
    application.state.authentication_runtime = replace(
        application.state.authentication_runtime, email_sender=sender
    )
    return application


def _client(application: FastAPI, peer: str = "203.0.113.7") -> AsyncClient:
    transport = ASGITransport(app=application, client=(peer, 1234))
    return AsyncClient(transport=transport, base_url=ORIGIN)


async def _sign_in(client: AsyncClient, email: str) -> str:
    response = await client.post("/sign-in", content=f"email={quote_plus(email)}", headers=FORM)
    assert (response.status_code, response.headers["location"]) == (303, "/sign-in/sent")
    return response.text


@pytest.mark.anyio
async def test_sign_in_visit_a_page_and_sign_out(
    settings: Settings, migrated_database_url: str
) -> None:
    sender = RecordingEmailSender()
    application = _application(settings, migrated_database_url, sender)
    async with _client(application) as client:
        await _sign_in(client, "ada@example.com")
        [message] = sender.messages
        link = urlsplit(message.magic_link)
        assert f"{link.scheme}://{link.netloc}" == ORIGIN

        confirmation = await client.get(f"{link.path}?{link.query}")
        fields = {
            str(field.attrs["name"]): str(field.attrs["value"])
            for field in parse_html(confirmation.text).find_all("input", type="hidden")
        }
        # The Origin a browser sends with this form depends on the page's referrer policy
        # (Fetch standard): under no-referrer it would be "null" and sign-in would fail.
        policy = confirmation.headers["referrer-policy"]
        browser_origin = "null" if policy == "no-referrer" else ORIGIN
        signed_in = await client.post(
            "/auth/magic-link",
            content="&".join(f"{name}={quote_plus(value)}" for name, value in fields.items()),
            headers={**FORM, "Origin": browser_origin},
        )
        assert (signed_in.status_code, signed_in.headers["location"]) == (303, "/dashboard")
        session_cookie = client.cookies[AUTHENTICATED_SESSION_COOKIE_NAME]

        dashboard = await client.get("/dashboard")
        assert dashboard.status_code == 200
        assert (await client.get("/sign-in")).headers["location"] == "/dashboard"

        # What the Sign out button sends through htmx.
        signed_out = await client.post(
            "/auth/logout",
            headers={
                "Origin": ORIGIN,
                CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME],
                "HX-Request": "true",
            },
        )
        assert signed_out.status_code == 200

    async with _client(application) as stale:
        stale.cookies.set(AUTHENTICATED_SESSION_COOKIE_NAME, session_cookie)
        after = await stale.get("/dashboard")
    assert (after.status_code, after.headers["location"]) == (303, "/sign-in")


@pytest.mark.anyio
async def test_known_and_unknown_addresses_look_the_same(
    settings: Settings, migrated_database_url: str, db_session: Session
) -> None:
    now = datetime.now(UTC)
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=now)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        EmailIdentityRecord(
            user_id=user.id,
            canonical_email="known@example.com",
            display_email="known@example.com",
            verified_at=now,
            created_at=now,
        )
    )
    db_session.commit()
    sender = RecordingEmailSender()
    application = _application(settings, migrated_database_url, sender)
    async with _client(application) as client:
        known = await client.post("/sign-in", content="email=known%40example.com", headers=FORM)
        unknown = await client.post(
            "/sign-in", content="email=stranger%40example.com", headers=FORM
        )

    assert known.status_code == unknown.status_code == 303
    assert known.headers["location"] == unknown.headers["location"] == "/sign-in/sent"
    assert known.text == unknown.text
    assert [message.recipient_email for message in sender.messages] == [
        "known@example.com",
        "stranger@example.com",
    ]


@pytest.mark.anyio
async def test_rate_limited_requests_look_the_same_and_send_nothing(
    settings: Settings, migrated_database_url: str
) -> None:
    sender = RecordingEmailSender()
    application = _application(settings, migrated_database_url, sender)
    async with _client(application) as client:
        for _ in range(5):
            await _sign_in(client, "busy@example.com")

    # Three deliveries per address per window (AUTH-03); the rest are silently dropped.
    assert len(sender.messages) == 3
