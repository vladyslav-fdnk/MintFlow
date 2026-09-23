import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import QueryParams

from mintflow.application.analytics import (
    BuildDashboard,
    CategorySpending,
    Comparison,
    Dashboard,
    DashboardDetail,
    DashboardPeriod,
    DashboardUserNotFound,
    MerchantSpending,
)
from mintflow.domain.capture import CurrencyCode
from mintflow.http.authentication import (
    GENERIC_UNAUTHENTICATED_MESSAGE,
    AuthenticatedPrincipalDependency,
    DatabaseSession,
    authentication_security_headers,
)
from mintflow.http.capture import CaptureRuntimeDependency, invalid_query
from mintflow.infrastructure.persistence import (
    SqlAlchemyAnalyticsRepository,
    SqlAlchemyUserRepository,
)

_DASHBOARD_QUERY_PARAMETERS: Final = frozenset({"date_from", "date_to", "currency"})
_ISO_DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

type Json = dict[str, object]

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def get_build_dashboard(
    session: DatabaseSession, runtime: CaptureRuntimeDependency
) -> BuildDashboard:
    return BuildDashboard(
        analytics=SqlAlchemyAnalyticsRepository(session),
        user_repository=SqlAlchemyUserRepository(session),
        clock=runtime.clock,
    )


BuildDashboardDependency = Annotated[BuildDashboard, Depends(get_build_dashboard)]


@dataclass(frozen=True, slots=True)
class DashboardQuery:
    period: DashboardPeriod | None
    currency: CurrencyCode | None


def parse_dashboard_query(request: Request) -> DashboardQuery | None:
    """Parse by hand, returning None on any invalid input.

    Like the history endpoint, this avoids FastAPI query validation, whose
    422 body echoes the submitted values.
    """
    return parse_dashboard_params(request.query_params)


def parse_dashboard_params(params: QueryParams) -> DashboardQuery | None:
    """The rules of ``parse_dashboard_query``, for callers that prepare the parameters."""
    if not set(params.keys()) <= _DASHBOARD_QUERY_PARAMETERS:
        return None
    values: dict[str, str | None] = {}
    for name in _DASHBOARD_QUERY_PARAMETERS:
        found = params.getlist(name)
        if len(found) > 1:
            return None
        values[name] = found[0] if found else None
    date_from, date_to, currency = values["date_from"], values["date_to"], values["currency"]
    if (date_from is None) != (date_to is None):
        return None
    try:
        period = (
            DashboardPeriod(date_from=_iso_date(date_from), date_to=_iso_date(date_to))
            if date_from is not None and date_to is not None
            else None
        )
        return DashboardQuery(
            period=period, currency=CurrencyCode(currency) if currency is not None else None
        )
    except ValueError:
        return None


def _iso_date(value: str) -> date:
    if not _ISO_DATE_PATTERN.fullmatch(value):
        raise ValueError("expected an ISO date")
    return date.fromisoformat(value)


def _period_json(period: DashboardPeriod) -> Json:
    return {"date_from": period.date_from.isoformat(), "date_to": period.date_to.isoformat()}


def _comparison_json(comparison: Comparison | None) -> Json | None:
    if comparison is None:
        return None
    return {
        "current_period": _period_json(comparison.current_period),
        "current_total_minor_units": comparison.current_total_minor_units,
        "previous_period": _period_json(comparison.previous_period),
        "previous_total_minor_units": comparison.previous_total_minor_units,
        "change_minor_units": comparison.change_minor_units,
        "change_basis_points": comparison.change_basis_points,
    }


def _category_json(category: CategorySpending) -> Json:
    return {
        "category_key": category.category_key,
        "category_name": category.category_name,
        "total_minor_units": category.total_minor_units,
        "count": category.count,
        "share_basis_points": category.share_basis_points,
    }


def _merchant_json(merchant: MerchantSpending) -> Json:
    return {
        "merchant": merchant.merchant,
        "total_minor_units": merchant.total_minor_units,
        "count": merchant.count,
        "share_basis_points": merchant.share_basis_points,
    }


def _detail_json(detail: DashboardDetail) -> Json:
    largest_expense = detail.insights.largest_expense
    largest_category = detail.insights.largest_category
    return {
        "summary": {
            "total_minor_units": detail.summary.total_minor_units,
            "count": detail.summary.count,
            "comparison": _comparison_json(detail.summary.comparison),
        },
        "spending_over_time": {
            "granularity": detail.spending_over_time.granularity.value,
            "buckets": [
                {
                    **_period_json(bucket.period),
                    "total_minor_units": bucket.total_minor_units,
                    "count": bucket.count,
                }
                for bucket in detail.spending_over_time.buckets
            ],
        },
        "categories": [_category_json(category) for category in detail.categories],
        "top_merchants": {
            "merchants": [_merchant_json(merchant) for merchant in detail.top_merchants.merchants],
            "other": (
                _merchant_json(detail.top_merchants.other)
                if detail.top_merchants.other is not None
                else None
            ),
        },
        "insights": {
            "largest_category": (
                _category_json(largest_category) if largest_category is not None else None
            ),
            "largest_expense": (
                {
                    "expense_id": str(largest_expense.expense_id),
                    "amount_minor_units": largest_expense.money.minor_units,
                    "currency": largest_expense.money.currency.value,
                    "merchant": largest_expense.merchant,
                    "transaction_date": largest_expense.transaction_date.isoformat(),
                }
                if largest_expense is not None
                else None
            ),
        },
    }


def dashboard_json(dashboard: Dashboard) -> Json:
    """Money as integer minor units with currency codes, dates as ISO strings (D1)."""
    return {
        "period": _period_json(dashboard.period),
        "currencies": [
            {
                "currency": total.currency.value,
                "total_minor_units": total.total_minor_units,
                "count": total.count,
            }
            for total in dashboard.currencies
        ],
        "currency": dashboard.currency.value if dashboard.currency is not None else None,
        "currency_selection_required": dashboard.currency_selection_required,
        "detail": _detail_json(dashboard.detail) if dashboard.detail is not None else None,
    }


def _unauthenticated_user() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=GENERIC_UNAUTHENTICATED_MESSAGE,
        headers=authentication_security_headers(),
    )


@router.get("/dashboard")
async def dashboard(
    request: Request,
    principal: AuthenticatedPrincipalDependency,
    build_dashboard: BuildDashboardDependency,
) -> JSONResponse:
    """The whole dashboard for one period and currency in one consistent response (D1)."""
    query = parse_dashboard_query(request)
    if query is None:
        raise invalid_query()
    try:
        result = build_dashboard.execute(
            caller_id=principal.user_id, period=query.period, currency=query.currency
        )
    except DashboardUserNotFound:
        raise _unauthenticated_user() from None
    return JSONResponse(dashboard_json(result), headers=authentication_security_headers())
