from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.config import Settings
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import AUTHENTICATED_SESSION_COOKIE_NAME, AuthenticationRuntime
from mintflow.http.capture import CaptureRuntime
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
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


def _application(database_url: str) -> FastAPI:
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
    application.state.telegram_runtime = TelegramRuntime(
        bot_api=RecordingTelegramBotApi(),
        bot_username="mintflow_test_bot",
        webhook_secret=SecretStr("hook-secret"),
        web_origin=ORIGIN,
        clock=lambda: NOW,
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


def _add(
    session: Session,
    owner_id: UUID,
    amount: int,
    day: date,
    *,
    currency: str = "EUR",
    merchant: str | None = None,
) -> Expense:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    expense = Expense.create(
        owner_id=owner_id,
        money=Money(minor_units=amount, currency=CurrencyCode(currency)),
        transaction_date=TransactionDate(day),
        category_key="groceries",
        capture_draft_id=draft.id,
        merchant=MerchantName(merchant) if merchant is not None else None,
        source=CaptureSource.WEB_MANUAL,
        now=NOW,
    )
    SqlAlchemyExpenseRepository(session).create(expense)
    return expense


async def _page(application: FastAPI, query: str = "") -> Response:
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    cookies = {AUTHENTICATED_SESSION_COOKIE_NAME: SECRET}
    async with AsyncClient(transport=transport, base_url=ORIGIN, cookies=cookies) as client:
        return await client.get(f"/dashboard{query}")


def _region(response: Response) -> Element:
    return parse_html(response.text).find(id="dashboard")


@pytest.mark.anyio
async def test_the_default_is_this_month_in_the_users_timezone_and_only_their_data(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET, timezone="Asia/Tokyo")
    stranger = _add_user(db_session)
    _add(db_session, owner, 1_200, date(2026, 9, 1), merchant="Bakery")
    _add(db_session, owner, 500, date(2026, 8, 31), merchant="Kiosk")
    _add(db_session, stranger, 99_999, date(2026, 9, 1), merchant="Stranger Shop")

    response = await _page(_application(migrated_database_url))

    assert response.status_code == 200
    region = _region(response)
    assert region.find("p", id="period-title").text == "Sep 1\u2009–\u200930, 2026 · EUR"
    assert region.find("p", class_="answer").text == f"You spent 12.00{NBSP}EUR"
    assert region.find("p", class_="answer-meta").text.startswith("1 expense")
    assert "Bakery" in region.text
    assert "Kiosk" not in region.text and "Stranger Shop" not in region.text
    assert "999.99" not in response.text


@pytest.mark.anyio
async def test_filters_choose_the_period_and_currency(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add(db_session, owner, 1_000, date(2026, 7, 10))
    _add(db_session, owner, 2_000, date(2026, 8, 10))
    _add(db_session, owner, 5_000, date(2026, 8, 12), currency="PLN")

    response = await _page(
        _application(migrated_database_url),
        "?date_from=2026-07-01&date_to=2026-08-31&currency=EUR",
    )

    region = _region(response)
    assert region.find("p", class_="answer").text == f"You spent 30.00{NBSP}EUR"
    assert region.find("h2", id="chart-title").text == "Spending by month"
    totals = region.find("ul", class_="currency-totals")
    assert [link.text for link in totals.find_all("a")] == [
        f"50.00{NBSP}PLN",
        f"30.00{NBSP}EUR",
    ]
    assert totals.find("a", aria_current="true").text == f"30.00{NBSP}EUR"


@pytest.mark.anyio
async def test_several_currencies_without_a_choice_are_never_added(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add(db_session, owner, 1_000, date(2026, 8, 10))
    _add(db_session, owner, 5_000, date(2026, 8, 12), currency="PLN")

    response = await _page(
        _application(migrated_database_url), "?date_from=2026-08-01&date_to=2026-08-31"
    )

    region = _region(response)
    assert "Choose a currency to see its details." in region.text
    assert region.find_all("p", class_="answer") == []
    assert region.find_all("section", class_="card chart-card") == []
    assert "60.00" not in region.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    ["?date_from=2026-09-01", "?date_from=2026-09-30&date_to=2026-09-01", "?currency=XYZ", "?x=1"],
)
async def test_invalid_filters_show_this_month_with_an_explanation(
    db_session: Session, migrated_database_url: str, query: str
) -> None:
    _add_user(db_session, secret=SECRET)

    response = await _page(_application(migrated_database_url), query)

    assert response.status_code == 400
    region = _region(response)
    assert region.find("p", role="alert").text.startswith("Those filters are not valid")
    assert region.find("p", id="period-title").text.startswith("Aug 1\u2009–\u200931, 2026")


@pytest.mark.anyio
async def test_empty_form_fields_mean_not_set(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_user(db_session, secret=SECRET)

    response = await _page(_application(migrated_database_url), "?date_from=&date_to=&currency=")

    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("linked", "action"), [(False, "Connect Telegram"), (True, "Open the bot")]
)
async def test_a_new_user_is_welcomed_and_pointed_to_telegram(
    db_session: Session, migrated_database_url: str, linked: bool, action: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    if linked:
        db_session.add(
            TelegramConnectionRecord(user_id=owner, telegram_user_id=4242, linked_at=NOW)
        )
        db_session.commit()

    response = await _page(_application(migrated_database_url))

    welcome = _region(response).find("section", aria_labelledby="welcome-title")
    assert welcome.find("a", class_="button").text == action


@pytest.mark.anyio
async def test_a_user_with_expenses_elsewhere_sees_an_empty_period_not_the_welcome(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add(db_session, owner, 1_000, date(2026, 1, 10))

    response = await _page(_application(migrated_database_url))

    region = _region(response)
    assert region.find_all("section", aria_labelledby="welcome-title") == []
    assert "No expenses in this period." in region.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "view"), [("", "columns"), ("?chart=pie", "pie"), ("?chart=radar", "columns")]
)
async def test_the_chart_view_is_chosen_in_the_url(
    db_session: Session, migrated_database_url: str, query: str, view: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add(db_session, owner, 1_200, date(2026, 8, 20))

    response = await _page(_application(migrated_database_url), query)

    assert response.status_code == 200
    region = _region(response)
    assert (region.find_all("svg", class_="donut") != []) is (view == "pie")
    assert (region.find_all("svg", class_="columns") != []) is (view == "columns")
    current = region.find("div", class_="segmented").find("a", aria_current="true")
    assert current.text == ("Pie" if view == "pie" else "Columns")
    assert region.find("input", name="chart").attrs["value"] == view


@pytest.mark.anyio
async def test_presets_follow_the_users_calendar(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_user(db_session, secret=SECRET, timezone="Asia/Tokyo")

    response = await _page(_application(migrated_database_url))

    presets = _region(response).find("div", class_="presets").find_all("a")
    # 23:30 UTC on 31 Aug is 1 Sep in Tokyo: this month is September.
    assert [(link.text, link.attrs.get("aria-current")) for link in presets] == [
        ("This month", "true"),
        ("Last month", None),
        ("Last 3 months", None),
    ]
    assert "date_from=2026-08-01&date_to=2026-08-31" in str(presets[1].attrs["href"])
