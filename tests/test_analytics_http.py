from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from mintflow.application.analytics import (
    CurrencyTotal,
    Dashboard,
    DashboardPeriod,
    DashboardUserNotFound,
)
from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.config import Settings
from mintflow.domain.capture import CurrencyCode
from mintflow.http.analytics import get_build_dashboard
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    get_authenticate_web_session,
)
from mintflow.main import create_app

SESSION_SECRET = "A" * 43
AUGUST = DashboardPeriod(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
EUR = CurrencyCode("EUR")
USD = CurrencyCode("USD")


class StubSessionAuthentication:
    def __init__(self, result: AuthenticatedWebSession | None) -> None:
        self.result = result

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        return self.result


class RecordingBuildDashboard:
    def __init__(self, result: Dashboard | Exception) -> None:
        self.result = result
        self.calls: list[tuple[UUID, DashboardPeriod | None, CurrencyCode | None]] = []

    def execute(
        self,
        *,
        caller_id: UUID,
        period: DashboardPeriod | None = None,
        currency: CurrencyCode | None = None,
    ) -> Dashboard:
        self.calls.append((caller_id, period, currency))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _selection_required() -> Dashboard:
    return Dashboard(
        period=AUGUST,
        currencies=(
            CurrencyTotal(currency=EUR, total_minor_units=3000, count=1),
            CurrencyTotal(currency=USD, total_minor_units=1000, count=1),
        ),
        currency=None,
        currency_selection_required=True,
        detail=None,
    )


def _app(
    settings: Settings, use_case: RecordingBuildDashboard, *, authenticated: bool = True
) -> tuple[FastAPI, UUID]:
    application = create_app(settings)
    user_id = uuid4()
    session = (
        AuthenticatedWebSession(session_id=uuid4(), user_id=user_id) if authenticated else None
    )
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(session)
    )
    application.dependency_overrides[get_build_dashboard] = lambda: use_case
    return application, user_id


async def _get(application: FastAPI, settings: Settings, query: str = "") -> Response:
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=settings.authentication_web_origin) as (
        client
    ):
        return await client.get(
            f"/analytics/dashboard{query}",
            cookies={AUTHENTICATED_SESSION_COOKIE_NAME: SESSION_SECRET},
        )


@pytest.mark.anyio
async def test_dashboard_passes_the_parsed_query_to_the_use_case(settings: Settings) -> None:
    use_case = RecordingBuildDashboard(_selection_required())
    application, user_id = _app(settings, use_case)

    response = await _get(
        application, settings, "?date_from=2026-08-01&date_to=2026-08-31&currency=eur"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert use_case.calls == [(user_id, AUGUST, EUR)]


@pytest.mark.anyio
async def test_default_request_lets_the_use_case_choose_period_and_currency(
    settings: Settings,
) -> None:
    use_case = RecordingBuildDashboard(_selection_required())
    application, user_id = _app(settings, use_case)

    response = await _get(application, settings)

    assert response.status_code == 200
    assert use_case.calls == [(user_id, None, None)]


@pytest.mark.anyio
async def test_selection_required_response_keeps_separate_totals(settings: Settings) -> None:
    application, _user_id = _app(settings, RecordingBuildDashboard(_selection_required()))

    body = (await _get(application, settings)).json()

    assert body["currency"] is None
    assert body["currency_selection_required"] is True
    assert body["detail"] is None
    assert body["currencies"] == [
        {"currency": "EUR", "total_minor_units": 3000, "count": 1},
        {"currency": "USD", "total_minor_units": 1000, "count": 1},
    ]
    assert "total_minor_units" not in body  # never a combined total


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        pytest.param("?date_from=2026-08-01", id="unpaired date"),
        pytest.param("?date_from=secret-input&date_to=2026-08-31", id="bad date"),
        pytest.param("?date_from=2026-08-31&date_to=2026-08-01", id="inverted"),
        pytest.param("?date_from=2026-01-01&date_to=2027-06-30", id="too long"),
        pytest.param("?currency=secret-input", id="bad currency"),
        pytest.param("?currency=EUR&currency=USD", id="repeated currency"),
        pytest.param("?merchant=secret-input", id="unknown parameter"),
    ],
)
async def test_invalid_queries_are_rejected_without_echo_or_use_case_call(
    settings: Settings, query: str
) -> None:
    use_case = RecordingBuildDashboard(_selection_required())
    application, _user_id = _app(settings, use_case)

    response = await _get(application, settings, query)

    assert response.status_code == 422
    assert response.json() == {"detail": "The request is invalid."}
    assert "secret-input" not in response.text
    assert use_case.calls == []


@pytest.mark.anyio
async def test_dashboard_requires_authentication(settings: Settings) -> None:
    use_case = RecordingBuildDashboard(_selection_required())
    application, _user_id = _app(settings, use_case, authenticated=False)

    response = await _get(application, settings)

    assert response.status_code == 401
    assert use_case.calls == []


@pytest.mark.anyio
async def test_a_session_without_a_user_record_is_unauthenticated(settings: Settings) -> None:
    application, _user_id = _app(
        settings, RecordingBuildDashboard(DashboardUserNotFound("user not found"))
    )

    response = await _get(application, settings)

    assert response.status_code == 401
