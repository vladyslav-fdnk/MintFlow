"""Deleting the account from the Web (docs/account_deletion_design.md, A3, A4)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from urllib.parse import quote_plus, urlsplit
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication.login_challenge import MagicLinkMessage
from mintflow.config import Settings
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    Money,
    TransactionDate,
)
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
)
from mintflow.infrastructure.persistence.models import (
    EmailIdentityRecord,
    ExpenseRecord,
    UserRecord,
)
from mintflow.main import create_app
from mintflow.web.testing import parse_html

pytestmark = pytest.mark.integration

ORIGIN = "https://app.mintflow.test"
EMAIL = "ada@example.com"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Origin": ORIGIN}


class RecordingEmailSender:
    def __init__(self) -> None:
        self.messages: list[MagicLinkMessage] = []

    def send_magic_link(self, message: MagicLinkMessage) -> None:
        self.messages.append(message)


@dataclass
class Browser:
    application: FastAPI
    client: AsyncClient
    sender: RecordingEmailSender
    session: Session

    async def sign_in(self, email: str = EMAIL) -> UUID:
        requested = await self.client.post(
            "/sign-in", content=f"email={quote_plus(email)}", headers=FORM
        )
        assert requested.headers["location"] == "/sign-in/sent"
        link = urlsplit(self.sender.messages[-1].magic_link)
        page = await self.client.get(f"{link.path}?{link.query}")
        fields = {
            str(field.attrs["name"]): str(field.attrs["value"])
            for field in parse_html(page.text).find_all("input", type="hidden")
        }
        signed_in = await self.client.post(
            "/auth/magic-link",
            content="&".join(f"{name}={quote_plus(value)}" for name, value in fields.items()),
            headers=FORM,
        )
        assert signed_in.headers["location"] == "/dashboard"
        self.session.expire_all()
        user_id = self.session.scalar(
            select(EmailIdentityRecord.user_id).where(EmailIdentityRecord.canonical_email == email)
        )
        assert user_id is not None
        return user_id

    async def delete(self, typed: str, *, csrf: bool = True) -> Response:
        headers = {**FORM, "HX-Request": "true", "Accept": "text/html"}
        if csrf:
            headers[CSRF_HEADER_NAME] = self.client.cookies[CSRF_COOKIE_NAME]
        return await self.client.post(
            "/settings/delete-account", content=f"email={quote_plus(typed)}", headers=headers
        )

    def expenses_of(self, user_id: UUID) -> int:
        self.session.expire_all()
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(ExpenseRecord)
                .where(ExpenseRecord.owner_id == user_id)
            )
            or 0
        )

    def user_exists(self, user_id: UUID) -> bool:
        self.session.expire_all()
        return self.session.get(UserRecord, user_id) is not None


@pytest.fixture
async def browser(
    settings: Settings, migrated_database_url: str, db_session: Session
) -> AsyncIterator[Browser]:
    sender = RecordingEmailSender()
    application = create_app(
        settings.model_copy(update={"database_url": SecretStr(migrated_database_url)})
    )
    application.state.authentication_runtime = replace(
        application.state.authentication_runtime, email_sender=sender
    )
    transport = ASGITransport(app=application, client=("203.0.113.7", 1234))
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        yield Browser(application, client, sender, db_session)
    application.state.database_engine.dispose()


def _add_expense(session: Session, owner_id: UUID) -> None:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    SqlAlchemyExpenseRepository(session).create(
        Expense.create(
            owner_id=owner_id,
            money=Money(minor_units=1250, currency=CurrencyCode("EUR")),
            transaction_date=TransactionDate(date(2026, 9, 20)),
            category_key="groceries",
            capture_draft_id=draft.id,
            merchant=None,
            source=CaptureSource.WEB_MANUAL,
            now=NOW,
        )
    )


@pytest.mark.anyio
async def test_settings_leads_to_a_page_that_says_what_is_deleted(browser: Browser) -> None:
    await browser.sign_in()

    settings = parse_html((await browser.client.get("/settings")).text)
    section = settings.find("section", aria_labelledby="delete-account-title")
    assert section.find("a").attrs["href"] == "/settings/delete-account"
    page = await browser.client.get("/settings/delete-account")

    assert page.status_code == 200
    text = parse_html(page.text).text
    assert "cannot be undone" in text and "30 days" in text
    assert parse_html(page.text).find("input", name="email").attrs["type"] == "email"


@pytest.mark.anyio
async def test_a_wrong_address_deletes_nothing(browser: Browser) -> None:
    user_id = await browser.sign_in()
    _add_expense(browser.session, user_id)

    response = await browser.delete("someone@example.com")

    assert response.status_code == 422
    error = parse_html(response.text).find("p", id="email-error")
    assert "Nothing was deleted" in error.text
    assert browser.user_exists(user_id) and browser.expenses_of(user_id) == 1


@pytest.mark.anyio
async def test_without_the_csrf_header_nothing_is_deleted(browser: Browser) -> None:
    user_id = await browser.sign_in()

    response = await browser.delete(EMAIL, csrf=False)

    assert response.status_code == 403
    assert browser.user_exists(user_id)


@pytest.mark.anyio
async def test_a_visitor_cannot_reach_the_page(browser: Browser) -> None:
    page = await browser.client.get("/settings/delete-account")

    assert (page.status_code, page.headers["location"]) == (303, "/sign-in")


@pytest.mark.anyio
async def test_deleting_signs_out_and_a_new_sign_in_starts_an_empty_account(
    browser: Browser,
) -> None:
    old_id = await browser.sign_in()
    _add_expense(browser.session, old_id)
    old_session = browser.client.cookies[AUTHENTICATED_SESSION_COOKIE_NAME]

    # Surrounding spaces and the domain's case do not matter, exactly as at sign-in.
    response = await browser.delete("  ada@EXAMPLE.com ")

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/account-deleted"
    cleared = response.headers.get_list("set-cookie")
    for name in (AUTHENTICATED_SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        assert any(cookie.startswith(f"{name}=") and "Max-Age=0" in cookie for cookie in cleared)
    assert not browser.user_exists(old_id) and browser.expenses_of(old_id) == 0

    deleted_page = await browser.client.get("/account-deleted")
    assert deleted_page.status_code == 200
    assert parse_html(deleted_page.text).find("a", class_="button button-wide").attrs["href"] == (
        "/sign-in"
    )

    browser.client.cookies.clear()
    browser.client.cookies.set(AUTHENTICATED_SESSION_COOKIE_NAME, old_session)
    stale = await browser.client.get("/dashboard")
    assert (stale.status_code, stale.headers["location"]) == (303, "/sign-in")

    browser.client.cookies.clear()
    new_id = await browser.sign_in()
    assert new_id != old_id
    assert browser.expenses_of(new_id) == 0
    assert (await browser.client.get("/dashboard")).status_code == 200


@pytest.mark.anyio
async def test_the_pages_speak_russian(browser: Browser) -> None:
    await browser.sign_in()
    browser.session.execute(update(UserRecord).values(ui_language="ru"))
    browser.session.commit()

    page = parse_html((await browser.client.get("/settings/delete-account")).text)
    assert page.find("h1").text == "Удалить аккаунт?"
    # The public page after deletion follows the browser's language.
    deleted = await browser.client.get("/account-deleted", headers={"Accept-Language": "ru"})
    assert parse_html(deleted.text).find("h1").text == "Ваш аккаунт удалён"


@pytest.mark.anyio
async def test_the_address_must_match_the_way_sign_in_does(browser: Browser) -> None:
    user_id = await browser.sign_in()

    # Sign-in keeps the case before "@", so this would be another account and deletes nothing.
    response = await browser.delete("ADA@example.com")

    assert response.status_code == 422
    assert browser.user_exists(user_id)
