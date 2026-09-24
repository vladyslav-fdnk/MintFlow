from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.application.rates import ExchangeRate
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
    SqlAlchemyExchangeRateRepository,
    SqlAlchemyExpenseRepository,
)
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord
from mintflow.main import create_app
from mintflow.web.testing import Element, parse_html

pytestmark = pytest.mark.integration

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
    category: str = "groceries",
) -> Expense:
    draft = CaptureDraft.start(owner_id=owner_id, source=CaptureSource.WEB_MANUAL, now=NOW)
    SqlAlchemyCaptureDraftRepository(session).create(draft)
    expense = Expense.create(
        owner_id=owner_id,
        money=Money(minor_units=amount, currency=CurrencyCode(currency)),
        transaction_date=TransactionDate(day),
        category_key=category,
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
        return await client.get(f"/expenses{query}")


def _rows(response: Response) -> list[Element]:
    page = parse_html(response.text)
    tables = page.find_all("tbody", id="expense-rows")
    return tables[0].find_all("tr") if tables else []


def _merchants(response: Response) -> list[str]:
    return [row.find_all("td")[1].text for row in _rows(response)]


def _next_href(response: Response) -> str | None:
    links = parse_html(response.text).find("div", id="load-more").find_all("a")
    return str(links[0].attrs["href"]) if links else None


@pytest.mark.anyio
async def test_the_history_lists_only_the_owners_active_expenses_newest_first(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    stranger = _add_user(db_session)
    _add(db_session, owner, 1_000, date(2026, 8, 1), merchant="Older")
    _add(db_session, owner, 2_000, date(2026, 8, 20), merchant="Newer")
    deleted = _add(db_session, owner, 3_000, date(2026, 8, 25), merchant="Deleted")
    SqlAlchemyExpenseRepository(db_session).update(deleted.delete(now=NOW))
    _add(db_session, stranger, 4_000, date(2026, 8, 21), merchant="Stranger")

    response = await _page(_application(migrated_database_url))

    assert response.status_code == 200
    assert _merchants(response) == ["Newer", "Older"]
    first = _rows(response)[0]
    assert first.find_all("td")[3].text == f"20.00{NBSP}EUR"
    assert str(first.find("a").attrs["href"]).startswith("/expenses/")
    assert _next_href(response) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?date_from=2026-08-10&date_to=2026-08-20", ["PLN food", "Transport"]),
        ("?category=transport", ["Transport"]),
        ("?currency=PLN", ["PLN food"]),
        ("?category=groceries&currency=EUR", ["EUR food"]),
        ("?date_from=2026-08-15", ["PLN food"]),
        ("?category=unknown", []),
    ],
)
async def test_each_filter_narrows_the_list(
    db_session: Session, migrated_database_url: str, query: str, expected: list[str]
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add(db_session, owner, 1_000, date(2026, 8, 1), merchant="EUR food")
    _add(db_session, owner, 2_000, date(2026, 8, 10), merchant="Transport", category="transport")
    _add(db_session, owner, 3_000, date(2026, 8, 20), merchant="PLN food", currency="PLN")

    response = await _page(_application(migrated_database_url), query)

    assert response.status_code == 200
    assert _merchants(response) == expected
    if not expected:
        assert "No expenses match these filters." in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(("count", "pages"), [(50, [50]), (51, [50, 1]), (101, [50, 50, 1])])
async def test_load_more_walks_every_expense_exactly_once(
    db_session: Session, migrated_database_url: str, count: int, pages: list[int]
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    for index in range(count):
        # Many share a date, so the order also depends on creation time and id.
        _add(db_session, owner, 100 + index, date(2026, 8, 1 + index % 5), merchant=f"M{index}")
    application = _application(migrated_database_url)

    seen: list[str] = []
    sizes: list[int] = []
    query: str | None = "?category=groceries"
    while query is not None:
        response = await _page(application, query)
        merchants = _merchants(response)
        sizes.append(len(merchants))
        seen += merchants
        href = _next_href(response)
        query = href.removeprefix("/expenses") if href is not None else None
        if href is not None:
            assert "category=groceries" in href

    assert sizes == pages
    assert sorted(seen) == sorted(f"M{index}" for index in range(count))


@pytest.mark.anyio
async def test_an_edit_between_pages_neither_repeats_nor_skips(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expenses = [
        _add(db_session, owner, 100, date(2026, 8, 1 + index % 20), merchant=f"M{index}")
        for index in range(60)
    ]
    application = _application(migrated_database_url)
    first = await _page(application)
    href = _next_href(first)
    assert href is not None
    # An amount edit keeps the ordering keys, so the second page is exactly the rest.
    edited = expenses[0]
    SqlAlchemyExpenseRepository(db_session).update(
        edited.edit_money(money=Money(minor_units=999, currency=CurrencyCode("EUR")), now=NOW)
    )
    db_session.commit()

    second = await _page(application, href.removeprefix("/expenses"))

    combined = _merchants(first) + _merchants(second)
    assert len(combined) == len(set(combined)) == 60


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        "?date_from=2026-09-30&date_to=2026-09-01",
        "?currency=XYZ",
        "?cursor=not-a-cursor",
        "?limit=5",
        "?unknown=1",
    ],
)
async def test_invalid_filters_explain_and_show_everything(
    db_session: Session, migrated_database_url: str, query: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add(db_session, owner, 1_000, date(2026, 8, 1), merchant="Kiosk")

    response = await _page(_application(migrated_database_url), query)

    assert response.status_code == 400
    assert (
        parse_html(response.text)
        .find("p", role="alert")
        .text.startswith("Those filters are not valid")
    )
    assert _merchants(response) == ["Kiosk"]


@pytest.mark.anyio
async def test_a_new_user_is_told_how_to_add_an_expense(
    db_session: Session, migrated_database_url: str
) -> None:
    _add_user(db_session, secret=SECRET)

    response = await _page(_application(migrated_database_url), "?date_from=&currency=")

    assert response.status_code == 200
    assert "No expenses yet." in response.text


def _seed_currencies(session: Session, **preferences: str) -> None:
    SqlAlchemyExchangeRateRepository(session).save(
        [ExchangeRate(CurrencyCode("USD"), Decimal("1.25"), date(2026, 8, 28), "ECB")],
        fetched_at=NOW,
    )
    owner = _add_user(session, secret=SECRET, **preferences)
    _add(session, owner, 1_000, date(2026, 8, 3), merchant="Euro Cafe")
    _add(session, owner, 3_000, date(2026, 8, 2), currency="USD", merchant="Dollar Diner")
    _add(session, owner, 10_000, date(2026, 8, 1), currency="BHD", merchant="Souq")


def _amount_cells(response: Response) -> dict[str, Element]:
    return {row.find_all("td")[1].text: row.find("td", class_="amount") for row in _rows(response)}


@pytest.mark.anyio
async def test_rows_in_another_currency_show_the_converted_amount_and_its_rates(
    db_session: Session, migrated_database_url: str
) -> None:
    _seed_currencies(db_session, default_currency="EUR")

    response = await _page(_application(migrated_database_url))

    cells = _amount_cells(response)
    converted = cells["Dollar Diner"].find("span", class_="converted")
    assert converted.text == "\u2248\u00a024.00\u00a0EUR"
    assert cells["Dollar Diner"].text.startswith("30.00\u00a0USD")
    # Its own currency, and a currency without a rate, show only their own amount.
    assert cells["Euro Cafe"].find_all("span", class_="converted") == []
    assert cells["Souq"].find_all("span", class_="converted") == []
    note = parse_html(response.text).find("p", class_="rates-note")
    assert note.text == (
        "\u2248 Converted into EUR at the exchange rates of Aug 28, 2026 (European Central Bank)."
    )


@pytest.mark.anyio
async def test_without_a_default_currency_history_rows_are_not_converted(
    db_session: Session, migrated_database_url: str
) -> None:
    _seed_currencies(db_session)

    response = await _page(_application(migrated_database_url))

    assert len(_rows(response)) == 3
    assert "\u2248" not in response.text
    assert parse_html(response.text).find_all("p", class_="rates-note") == []
