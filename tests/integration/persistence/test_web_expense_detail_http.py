from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode
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
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
)
from mintflow.http.capture import CaptureRuntime
from mintflow.infrastructure.persistence import (
    SqlAlchemyCaptureDraftRepository,
    SqlAlchemyExpenseRepository,
)
from mintflow.infrastructure.persistence.models import (
    ExpenseChangeRecordModel,
    ExpenseRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app
from mintflow.web.testing import parse_html

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


STRANGER_SECRET = "Z" * 43


def _client(application: FastAPI, secret: str = SECRET) -> AsyncClient:
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    cookies = {
        AUTHENTICATED_SESSION_COOKIE_NAME: secret,
        CSRF_COOKIE_NAME: runtime.csrf_digester.derive(session_secret=secret),
    }
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url=ORIGIN, cookies=cookies)


def _htmx(client: AsyncClient) -> dict[str, str]:
    """What htmx and app.js send with a mutation."""
    return {
        "Origin": ORIGIN,
        "HX-Request": "true",
        "Accept": "text/html",
        CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME],
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _form_values(client: AsyncClient, expense_id: object) -> dict[str, str]:
    """The edit form as the browser would submit it untouched."""
    page = parse_html((await client.get(f"/expenses/{expense_id}/edit")).text)
    form = page.find("form", id="edit-form")
    values = {
        str(field.attrs["name"]): str(field.attrs.get("value") or "")
        for field in form.find_all("input")
    }
    for select in form.find_all("select"):
        chosen = [option for option in select.find_all("option") if "selected" in option.attrs]
        values[str(select.attrs["name"])] = str(chosen[0].attrs["value"])
    values["note"] = form.find("textarea").text
    return values


async def _submit(client: AsyncClient, expense_id: object, values: dict[str, str]) -> Response:
    return await client.post(
        f"/expenses/{expense_id}/edit", content=urlencode(values), headers=_htmx(client)
    )


def _stored(session: Session, expense: Expense) -> ExpenseRecord:
    session.expire_all()
    record = session.get(ExpenseRecord, expense.id)
    assert record is not None
    return record


@pytest.mark.anyio
async def test_the_detail_page_shows_every_confirmed_field(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET, timezone="Asia/Tokyo")
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20), merchant="Corner Shop")

    async with _client(_application(migrated_database_url)) as client:
        response = await client.get(f"/expenses/{expense.id}")

    assert response.status_code == 200
    details = parse_html(response.text).find("dl")
    assert details.text == (
        f"Amount 12.50{NBSP}EUR Date Aug 20, 2026 Merchant Corner Shop Category Groceries"
        " Added from Web Confirmed Sep 1, 2026, 8:30:00\u202fAM"
    )


@pytest.mark.anyio
async def test_another_users_or_a_malformed_id_is_404_on_every_route(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    _add_user(db_session, secret=STRANGER_SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20))
    application = _application(migrated_database_url)

    async with _client(application, STRANGER_SECRET) as client:
        statuses = [
            (
                await client.get(f"/expenses/{target}{suffix}", headers={"Accept": "text/html"})
            ).status_code
            for target in (expense.id, "not-a-uuid")
            for suffix in ("", "/edit", "/delete", "/deleted")
        ]
        values = {"amount": "1", "currency": "EUR"}
        for action in ("edit", "delete", "restore"):
            response = await client.post(
                f"/expenses/{expense.id}/{action}",
                content=urlencode(values),
                headers=_htmx(client),
            )
            statuses.append(response.status_code)

    assert set(statuses) == {404}
    assert _stored(db_session, expense).amount_minor_units == 1_250


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("changes", "column", "expected"),
    [
        ({"amount": "20.00"}, "amount_minor_units", 2_000),
        ({"amount": "1500", "currency": "JPY"}, "amount_currency", "JPY"),
        ({"transaction_date": "2026-08-01"}, "transaction_date", date(2026, 8, 1)),
        ({"merchant": ""}, "merchant_name", None),
        ({"merchant": "  New   Name "}, "merchant_name", "New Name"),
        ({"category_key": "transport"}, "category_key", "transport"),
        ({"note": "Team lunch"}, "note", "Team lunch"),
    ],
)
async def test_a_valid_edit_is_saved_and_recorded(
    db_session: Session,
    migrated_database_url: str,
    changes: dict[str, str],
    column: str,
    expected: object,
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20), merchant="Corner Shop")

    async with _client(_application(migrated_database_url)) as client:
        values = await _form_values(client, expense.id)
        response = await _submit(client, expense.id, {**values, **changes})
        detail = await client.get(response.headers["hx-redirect"])

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == f"/expenses/{expense.id}?done=saved"
    assert getattr(_stored(db_session, expense), column) == expected
    assert parse_html(detail.text).find(id="status").text == "Changes saved."
    assert db_session.query(ExpenseChangeRecordModel).count() == 1


@pytest.mark.anyio
async def test_an_invalid_edit_keeps_what_was_typed_and_changes_nothing(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20), merchant="Corner Shop")

    async with _client(_application(migrated_database_url)) as client:
        values = await _form_values(client, expense.id)
        response = await _submit(
            client,
            expense.id,
            {**values, "amount": "12.505", "merchant": "Renamed", "transaction_date": "2026-09-03"},
        )

    assert response.status_code == 422
    form = parse_html(response.text).find("form", id="edit-form")
    amount = form.find("input", id="amount")
    assert (amount.attrs["value"], amount.attrs["aria-invalid"]) == ("12.505", "true")
    assert form.find(id="amount-error").text == "EUR amounts have at most 2 decimal places."
    date_field = form.find("input", id="transaction_date")
    # Today is 1 Sep in UTC, so 3 Sep is beyond the one-day tolerance.
    assert form.find(id="transaction_date-error").text.startswith("The date can be at most")
    assert date_field.attrs["value"] == "2026-09-03"
    assert form.find("input", id="merchant").attrs["value"] == "Renamed"
    stored = _stored(db_session, expense)
    assert (stored.amount_minor_units, stored.merchant_name) == (1_250, "Corner Shop")


@pytest.mark.anyio
async def test_a_stale_form_does_not_undo_a_change_made_meanwhile(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20), merchant="Corner Shop")
    application = _application(migrated_database_url)

    async with _client(application) as client:
        stale = await _form_values(client, expense.id)
        # Meanwhile, another tab renames the merchant.
        renamed = await _submit(
            client, expense.id, {**(await _form_values(client, expense.id)), "merchant": "Bakery"}
        )
        assert renamed.status_code == 200
        response = await _submit(client, expense.id, {**stale, "amount": "30.00"})

    assert response.status_code == 200
    stored = _stored(db_session, expense)
    assert (stored.amount_minor_units, stored.merchant_name) == (3_000, "Bakery")


@pytest.mark.anyio
async def test_an_untouched_form_writes_nothing(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20))

    async with _client(_application(migrated_database_url)) as client:
        response = await _submit(client, expense.id, await _form_values(client, expense.id))

    assert response.headers["hx-redirect"] == f"/expenses/{expense.id}"
    assert db_session.query(ExpenseChangeRecordModel).count() == 0


@pytest.mark.anyio
async def test_delete_then_undo_round_trip(db_session: Session, migrated_database_url: str) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20), merchant="Corner Shop")

    async with _client(_application(migrated_database_url)) as client:
        confirm = parse_html((await client.get(f"/expenses/{expense.id}/delete")).text)
        button = confirm.find("main").find("button")
        assert button.attrs["hx-post"] == f"/expenses/{expense.id}/delete"
        assert _stored(db_session, expense).deleted_at is None  # viewing asks first

        deleted = await client.post(button.attrs["hx-post"] or "", headers=_htmx(client))
        assert deleted.headers["hx-redirect"] == f"/expenses/{expense.id}/deleted"
        assert _stored(db_session, expense).deleted_at is not None
        assert "Corner Shop" not in (await client.get("/expenses")).text
        assert (await client.get(f"/expenses/{expense.id}")).status_code == 404

        undo_page = parse_html((await client.get(f"/expenses/{expense.id}/deleted")).text)
        undo = undo_page.find("main").find("button")
        restored = await client.post(undo.attrs["hx-post"] or "", headers=_htmx(client))
        assert restored.headers["hx-redirect"] == f"/expenses/{expense.id}?done=restored"
        detail = await client.get(restored.headers["hx-redirect"])

    assert parse_html(detail.text).find(id="status").text == "Expense restored."
    assert _stored(db_session, expense).deleted_at is None


@pytest.mark.anyio
async def test_the_deleted_page_exists_only_for_a_deleted_expense(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20))

    async with _client(_application(migrated_database_url)) as client:
        response = await client.get(f"/expenses/{expense.id}/deleted")

    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["edit", "delete", "restore"])
async def test_mutations_without_the_csrf_header_are_refused(
    db_session: Session, migrated_database_url: str, action: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20))

    async with _client(_application(migrated_database_url)) as client:
        headers = {**_htmx(client)}
        del headers[CSRF_HEADER_NAME]
        response = await client.post(
            f"/expenses/{expense.id}/{action}",
            content=urlencode({"amount": "99.00"}),
            headers=headers,
        )

    assert response.status_code == 403
    stored = _stored(db_session, expense)
    assert (stored.amount_minor_units, stored.deleted_at) == (1_250, None)


@pytest.mark.anyio
async def test_an_edit_shows_up_in_history_and_on_the_dashboard(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session, secret=SECRET)
    expense = _add(db_session, owner, 1_250, date(2026, 8, 20))

    async with _client(_application(migrated_database_url)) as client:
        values = await _form_values(client, expense.id)
        await _submit(client, expense.id, {**values, "amount": "77.00"})
        history = await client.get("/expenses")
        dashboard = await client.get("/dashboard?date_from=2026-08-01&date_to=2026-08-31")

    assert f"77.00{NBSP}EUR" in history.text
    assert f"Total spent 77.00{NBSP}EUR" in parse_html(dashboard.text).find("dl").text
