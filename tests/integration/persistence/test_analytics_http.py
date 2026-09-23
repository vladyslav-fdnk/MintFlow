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
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord
from mintflow.main import create_app

pytestmark = pytest.mark.integration

# 23:30 UTC on 31 Aug is already 1 Sep in Tokyo.
NOW = datetime(2026, 8, 31, 23, 30, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"


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
    auth_runtime: AuthenticationRuntime = application.state.authentication_runtime
    application.state.authentication_runtime = AuthenticationRuntime(
        session_factory=auth_runtime.session_factory,
        email_sender=auth_runtime.email_sender,
        link_builder=auth_runtime.link_builder,
        rate_limit_digester=auth_runtime.rate_limit_digester,
        csrf_digester=auth_runtime.csrf_digester,
        clock=lambda: NOW,
    )
    application.state.capture_runtime = CaptureRuntime(clock=lambda: NOW)
    return application


def _add_user(session: Session, *, timezone: str, secret: str | None = None) -> UUID:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    session.execute(update(UserRecord).where(UserRecord.id == user.id).values(timezone=timezone))
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


async def _dashboard(application: FastAPI, *, secret: str, query: str = "") -> Response:
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.get(
            f"/analytics/dashboard{query}", cookies={AUTHENTICATED_SESSION_COOKIE_NAME: secret}
        )


@pytest.mark.anyio
async def test_default_request_is_the_current_month_in_the_users_timezone(
    db_session: Session, migrated_database_url: str
) -> None:
    secret = "M" * 43
    owner_id = _add_user(db_session, timezone="Asia/Tokyo", secret=secret)
    stranger_id = _add_user(db_session, timezone="UTC")
    _add(db_session, owner_id, 1_200, date(2026, 9, 1), merchant="Bakery")
    # Today in Tokyo is 1 Sep, so the compared window is 1 Sep against 1 Aug.
    _add(db_session, owner_id, 800, date(2026, 8, 1), merchant="Bakery")
    _add(db_session, owner_id, 500, date(2026, 8, 31))  # previous month, outside the window
    deleted = _add(db_session, owner_id, 50_000, date(2026, 9, 1))
    SqlAlchemyExpenseRepository(db_session).update(deleted.delete(now=NOW))
    _add(db_session, stranger_id, 99_999, date(2026, 9, 1))
    application = _application(migrated_database_url)

    response = await _dashboard(application, secret=secret)

    assert response.status_code == 200
    body = response.json()
    assert body["period"] == {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    assert body["currency"] == "EUR"
    assert body["currencies"] == [{"currency": "EUR", "total_minor_units": 1_200, "count": 1}]
    detail = body["detail"]
    assert detail["summary"]["total_minor_units"] == 1_200
    comparison = detail["summary"]["comparison"]
    assert comparison is not None
    assert comparison["previous_period"] == {"date_from": "2026-08-01", "date_to": "2026-08-01"}
    assert (comparison["previous_total_minor_units"], comparison["change_minor_units"]) == (
        800,
        400,
    )
    assert comparison["change_basis_points"] == 5_000
    assert len(detail["spending_over_time"]["buckets"]) == 30
    assert detail["top_merchants"]["merchants"] == [
        {"merchant": "Bakery", "total_minor_units": 1_200, "count": 1, "share_basis_points": 10_000}
    ]
    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_multi_currency_request_through_the_real_route(
    db_session: Session, migrated_database_url: str
) -> None:
    secret = "N" * 43
    owner_id = _add_user(db_session, timezone="UTC", secret=secret)
    _add(db_session, owner_id, 3_000, date(2026, 8, 10))
    _add(db_session, owner_id, 1_000, date(2026, 8, 12), currency="USD", merchant="Store")
    application = _application(migrated_database_url)
    period = "?date_from=2026-08-01&date_to=2026-08-31"

    unselected = await _dashboard(application, secret=secret, query=period)
    selected = await _dashboard(application, secret=secret, query=f"{period}&currency=usd")

    assert unselected.status_code == selected.status_code == 200
    assert unselected.json()["currency_selection_required"] is True
    assert unselected.json()["detail"] is None
    assert [total["currency"] for total in unselected.json()["currencies"]] == ["EUR", "USD"]
    selected_body = selected.json()
    assert selected_body["currency"] == "USD"
    assert selected_body["detail"]["summary"]["total_minor_units"] == 1_000
    largest = selected_body["detail"]["insights"]["largest_expense"]
    assert (largest["amount_minor_units"], largest["currency"], largest["merchant"]) == (
        1_000,
        "USD",
        "Store",
    )
    application.state.database_engine.dispose()
