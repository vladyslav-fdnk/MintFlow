from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.telegram import ClaimTelegramLink
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
)
from mintflow.http.capture import CaptureRuntime
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
)
from mintflow.infrastructure.persistence.models import (
    TelegramConnectionRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi
from mintflow.web.testing import Element, parse_html

pytestmark = pytest.mark.integration

# 23:30 UTC on 31 Aug is already 1 Sep in Tokyo.
NOW = datetime(2026, 8, 31, 23, 30, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"
SECRET = "W" * 43
NBSP = "\u00a0"


def _application(database_url: str, *, telegram: bool = True) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            log_level="CRITICAL",
            database_url=SecretStr(database_url),
            authentication_rate_limit_key=SecretStr("integration-rate-limit-key"),
            authentication_csrf_signing_key=SecretStr("integration-csrf-signing-key"),
            authentication_web_origin=ORIGIN,
            authentication_return_targets=frozenset({"dashboard"}),
            email_backend=None,
        )
    )
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    application.state.authentication_runtime = AuthenticationRuntime(
        session_factory=runtime.session_factory,
        email_sender=runtime.email_sender,
        link_builder=runtime.link_builder,
        rate_limit_digester=runtime.rate_limit_digester,
        csrf_digester=runtime.csrf_digester,
        clock=lambda: NOW,
    )
    application.state.capture_runtime = CaptureRuntime(clock=lambda: NOW)
    application.state.telegram_runtime = (
        TelegramRuntime(
            bot_api=RecordingTelegramBotApi(),
            bot_username="mintflow_test_bot",
            webhook_secret=SecretStr("hook-secret"),
            web_origin=ORIGIN,
            clock=lambda: NOW,
        )
        if telegram
        else None
    )
    return application


def _add_user(session: Session, *, secret: str | None = None, **preferences: str) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    if preferences:
        session.execute(update(UserRecord).where(UserRecord.id == user.id).values(**preferences))
    if secret is not None:
        session.add(
            WebSessionRecord(
                user_id=user.id,
                secret_hash=hash_token(secret),
                issued_at=NOW - timedelta(hours=1),
                expires_at=NOW + timedelta(days=1),
            )
        )
    session.commit()
    return user.id


OTHER_SECRET = "Q" * 43


def _client(application: FastAPI, secret: str = SECRET) -> AsyncClient:
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    cookies = {
        AUTHENTICATED_SESSION_COOKIE_NAME: secret,
        CSRF_COOKIE_NAME: runtime.csrf_digester.derive(session_secret=secret),
    }
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url=ORIGIN, cookies=cookies)


def _htmx(client: AsyncClient, *, csrf: bool = True) -> dict[str, str]:
    headers = {
        "Origin": ORIGIN,
        "HX-Request": "true",
        "Accept": "text/html",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if csrf:
        headers[CSRF_HEADER_NAME] = client.cookies[CSRF_COOKIE_NAME]
    return headers


def _stored_user(session: Session, user_id: UUID) -> UserRecord:
    session.expire_all()
    record = session.get(UserRecord, user_id)
    assert record is not None
    return record


def _selected(page: Element, select_id: str) -> str:
    select = page.find("select", id=select_id)
    chosen = [option for option in select.find_all("option") if "selected" in option.attrs]
    return str(chosen[0].attrs["value"]) if chosen else ""


def _telegram_section(response: Response) -> Element:
    return parse_html(response.text).find("section", id="telegram")


@pytest.mark.anyio
async def test_the_page_shows_the_current_conventions(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_user(
        db_session, secret=SECRET, timezone="Asia/Tokyo", default_currency="PLN", locale="de-DE"
    )

    async with _client(_application(migrated_database_url)) as client:
        response = await client.get("/settings")

    page = parse_html(response.text)
    assert response.status_code == 200
    assert (
        _selected(page, "timezone"),
        _selected(page, "default_currency"),
        _selected(page, "locale"),
    ) == ("Asia/Tokyo", "PLN", "de-DE")
    assert "never converted" in page.find(id="currency-hint").text
    assert page.find("section", id="telegram").find("button").text == "Connect Telegram"


@pytest.mark.anyio
async def test_saving_changes_the_conventions_and_the_dashboards_month(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _add_user(db_session, secret=SECRET)
    application = _application(migrated_database_url)

    async with _client(application) as client:
        before = await client.get("/dashboard")
        response = await client.post(
            "/settings/preferences",
            content=urlencode(
                {"timezone": "Asia/Tokyo", "default_currency": "JPY", "locale": "en-GB"}
            ),
            headers=_htmx(client),
        )
        after = await client.get("/dashboard")
        saved = await client.get(response.headers["hx-redirect"])

    assert response.headers["hx-redirect"] == "/settings?done=saved"
    stored = _stored_user(db_session, user_id)
    assert (stored.timezone, stored.default_currency, stored.locale) == (
        "Asia/Tokyo",
        "JPY",
        "en-GB",
    )
    # 23:30 UTC on 31 Aug is already September in Tokyo.
    assert "Aug" in parse_html(before.text).find("h2", id="period-title").text
    assert "Sept" in parse_html(after.text).find("h2", id="period-title").text
    assert parse_html(saved.text).find(id="status").text == "Settings saved."


@pytest.mark.anyio
async def test_currency_and_format_can_be_cleared(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _add_user(db_session, secret=SECRET, default_currency="EUR", locale="de-DE")

    async with _client(_application(migrated_database_url)) as client:
        await client.post(
            "/settings/preferences",
            content=urlencode({"timezone": "UTC", "default_currency": "", "locale": ""}),
            headers=_htmx(client),
        )

    stored = _stored_user(db_session, user_id)
    assert (stored.default_currency, stored.locale) == (None, None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"timezone": "Mars/Base", "default_currency": "PLN", "locale": ""}, "timezone"),
        ({"timezone": "UTC", "default_currency": "XYZ", "locale": ""}, "default_currency"),
        ({"timezone": "UTC", "default_currency": "", "locale": "not a tag"}, "locale"),
    ],
)
async def test_an_invalid_value_is_reported_on_its_field_and_nothing_changes(
    db_session: Session, migrated_database_url: str, values: dict[str, str], field: str
) -> None:
    user_id = _add_user(db_session, secret=SECRET, default_currency="EUR")

    async with _client(_application(migrated_database_url)) as client:
        response = await client.post(
            "/settings/preferences", content=urlencode(values), headers=_htmx(client)
        )

    assert response.status_code == 422
    form = parse_html(response.text).find("form", id="preferences-form")
    assert form.find(id=field).attrs["aria-invalid"] == "true"
    assert form.find(id=f"{field}-error").text.startswith("Choose")
    stored = _stored_user(db_session, user_id)
    assert (stored.timezone, stored.default_currency) == ("UTC", "EUR")


@pytest.mark.anyio
async def test_preferences_need_the_csrf_header(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _add_user(db_session, secret=SECRET)

    async with _client(_application(migrated_database_url)) as client:
        response = await client.post(
            "/settings/preferences",
            content=urlencode({"timezone": "Asia/Tokyo"}),
            headers=_htmx(client, csrf=False),
        )

    assert response.status_code == 403
    assert _stored_user(db_session, user_id).timezone == "UTC"


@pytest.mark.anyio
async def test_linking_telegram_from_settings(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _add_user(db_session, secret=SECRET)
    _add_user(db_session, secret=OTHER_SECRET)
    application = _application(migrated_database_url)
    bot = application.state.telegram_runtime.bot_api

    async with _client(application) as client:
        started = await client.post("/settings/telegram/link", headers=_htmx(client))
        section = _telegram_section(started)
        deep_link = str(section.find("a", class_="button").attrs["href"])
        assert deep_link.startswith("https://t.me/mintflow_test_bot?start=")
        status = section.find("div", id="link-status")
        poll_url = str(status.attrs["hx-get"])
        assert status.attrs["hx-trigger"] == "every 2s"

        # Nothing has happened in Telegram yet: nothing to swap.
        assert (await client.get(poll_url, headers=_htmx(client))).status_code == 204
        # Another Web session cannot see this link.
        async with _client(application, OTHER_SECRET) as other:
            assert (await other.get(poll_url, headers={"Accept": "text/html"})).status_code == 404

        # The user presses Start in the bot.
        assert ClaimTelegramLink(
            repository=SqlAlchemyTelegramLinkRepository(db_session), clock=lambda: NOW
        ).execute(payload=deep_link.rsplit("=", 1)[1], telegram_user_id=4242, display_name="Ada L")
        db_session.commit()

        claimed = parse_html((await client.get(poll_url, headers=_htmx(client))).text)
        assert "Ada L" in claimed.text
        confirm = claimed.find("button")
        assert "hx-trigger" not in claimed.find("div", id="link-status").attrs
        confirmed = await client.post(str(confirm.attrs["hx-post"]), headers=_htmx(client))
        assert confirmed.headers["hx-redirect"] == "/settings?done=connected"
        settings = await client.get("/settings")

    assert "Connected as Ada L" in _telegram_section(settings).text
    assert (
        db_session.scalar(
            select(TelegramConnectionRecord.user_id).where(
                TelegramConnectionRecord.unlinked_at.is_(None)
            )
        )
        == user_id
    )
    assert [call.arguments["chat_id"] for call in bot.calls_to("send_message")] == [4242]


@pytest.mark.anyio
async def test_disconnecting_asks_first_then_unlinks(
    db_session: Session, migrated_database_url: str
) -> None:
    user_id = _add_user(db_session, secret=SECRET)
    db_session.add(
        TelegramConnectionRecord(
            user_id=user_id, telegram_user_id=4242, telegram_display_name="Ada L", linked_at=NOW
        )
    )
    db_session.commit()

    async with _client(_application(migrated_database_url)) as client:
        confirm_page = parse_html((await client.get("/settings/telegram/disconnect")).text)
        button = confirm_page.find("main").find("button")
        assert "Ada L" in confirm_page.find("main").text
        done = await client.post(str(button.attrs["hx-post"]), headers=_htmx(client))
        settings = await client.get("/settings")
        again = await client.get("/settings/telegram/disconnect", headers={"Accept": "text/html"})

    assert done.headers["hx-redirect"] == "/settings?done=disconnected"
    assert "Connect Telegram" in _telegram_section(settings).text
    assert again.status_code == 404
    db_session.expire_all()
    assert db_session.scalar(select(TelegramConnectionRecord.unlinked_at)) is not None


@pytest.mark.anyio
async def test_without_telegram_the_page_says_so_and_linking_is_404(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_user(db_session, secret=SECRET)

    async with _client(_application(migrated_database_url, telegram=False)) as client:
        page = await client.get("/settings")
        link = await client.post("/settings/telegram/link", headers=_htmx(client))

    assert "not set up on this server" in _telegram_section(page).text
    assert link.status_code == 404
