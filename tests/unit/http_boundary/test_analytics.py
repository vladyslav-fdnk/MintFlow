import json
from datetime import date
from uuid import uuid4

import pytest
from fastapi import Request

from mintflow.application.analytics import (
    BucketGranularity,
    CategorySpending,
    Comparison,
    CurrencyTotal,
    Dashboard,
    DashboardDetail,
    DashboardPeriod,
    Insights,
    LargestExpense,
    MerchantSpending,
    SpendingOverTime,
    Summary,
    TimeBucket,
    TopMerchants,
)
from mintflow.domain.capture import CurrencyCode, Money
from mintflow.http.analytics import DashboardQuery, dashboard_json, parse_dashboard_query

AUGUST = DashboardPeriod(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
EUR = CurrencyCode("EUR")


def _request(query_string: bytes) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/analytics/dashboard",
            "headers": [],
            "query_string": query_string,
        }
    )


def test_no_parameters_means_default_period_and_no_currency() -> None:
    assert parse_dashboard_query(_request(b"")) == DashboardQuery(period=None, currency=None)


def test_parses_period_and_normalizes_currency() -> None:
    query = parse_dashboard_query(_request(b"date_from=2026-08-01&date_to=2026-08-31&currency=eur"))

    assert query == DashboardQuery(period=AUGUST, currency=EUR)


@pytest.mark.parametrize(
    "query_string",
    [
        pytest.param(b"date_from=2026-08-01", id="date_from without date_to"),
        pytest.param(b"date_to=2026-08-31", id="date_to without date_from"),
        pytest.param(b"date_from=2026-8-1&date_to=2026-08-31", id="non-iso date"),
        pytest.param(b"date_from=20260801&date_to=20260831", id="compact date"),
        pytest.param(b"date_from=2026-02-30&date_to=2026-03-01", id="impossible date"),
        pytest.param(b"date_from=2026-08-31&date_to=2026-08-01", id="inverted range"),
        pytest.param(b"date_from=2026-01-01&date_to=2027-01-02", id="over 366 days"),
        pytest.param(b"date_from=1999-12-01&date_to=1999-12-31", id="before 2000"),
        pytest.param(b"currency=ZZZ", id="unsupported currency"),
        pytest.param(b"currency=EUR&currency=USD", id="repeated currency"),
        pytest.param(
            b"date_from=2026-08-01&date_from=2026-08-02&date_to=2026-08-31", id="repeated date"
        ),
        pytest.param(b"category=groceries", id="unknown parameter"),
    ],
)
def test_rejects_invalid_parameters(query_string: bytes) -> None:
    assert parse_dashboard_query(_request(query_string)) is None


def _detail() -> DashboardDetail:
    groceries = CategorySpending(
        category_key="groceries",
        category_name="Groceries",
        total_minor_units=1500,
        count=2,
        share_basis_points=10_000,
    )
    return DashboardDetail(
        summary=Summary(
            total_minor_units=1500,
            count=2,
            comparison=Comparison(
                current_period=DashboardPeriod(
                    date_from=date(2026, 8, 1), date_to=date(2026, 8, 20)
                ),
                current_total_minor_units=1500,
                previous_period=DashboardPeriod(
                    date_from=date(2026, 7, 1), date_to=date(2026, 7, 20)
                ),
                previous_total_minor_units=1000,
                change_minor_units=500,
                change_basis_points=5000,
            ),
        ),
        spending_over_time=SpendingOverTime(
            granularity=BucketGranularity.DAY,
            buckets=(
                TimeBucket(
                    period=DashboardPeriod(date_from=date(2026, 8, 1), date_to=date(2026, 8, 1)),
                    total_minor_units=1500,
                    count=2,
                ),
            ),
        ),
        categories=(groceries,),
        top_merchants=TopMerchants(
            merchants=(
                MerchantSpending(
                    merchant="Shop", total_minor_units=1000, count=1, share_basis_points=6667
                ),
            ),
            other=MerchantSpending(
                merchant=None, total_minor_units=500, count=1, share_basis_points=3333
            ),
        ),
        insights=Insights(
            largest_category=groceries,
            largest_expense=LargestExpense(
                expense_id=uuid4(),
                money=Money(minor_units=1000, currency=EUR),
                merchant="Shop",
                transaction_date=date(2026, 8, 1),
            ),
        ),
    )


def test_dashboard_json_is_json_serializable_with_integer_money_and_iso_dates() -> None:
    dashboard = Dashboard(
        period=AUGUST,
        currencies=(CurrencyTotal(currency=EUR, total_minor_units=1500, count=2),),
        currency=EUR,
        currency_selection_required=False,
        detail=_detail(),
    )

    body = json.loads(json.dumps(dashboard_json(dashboard)))

    assert body["period"] == {"date_from": "2026-08-01", "date_to": "2026-08-31"}
    assert body["currencies"] == [{"currency": "EUR", "total_minor_units": 1500, "count": 2}]
    assert body["currency"] == "EUR"
    detail = body["detail"]
    assert detail["summary"]["comparison"]["previous_period"] == {
        "date_from": "2026-07-01",
        "date_to": "2026-07-20",
    }
    assert detail["summary"]["comparison"]["change_basis_points"] == 5000
    assert detail["spending_over_time"] == {
        "granularity": "day",
        "buckets": [
            {
                "date_from": "2026-08-01",
                "date_to": "2026-08-01",
                "total_minor_units": 1500,
                "count": 2,
            }
        ],
    }
    assert detail["categories"][0]["share_basis_points"] == 10_000
    assert detail["top_merchants"]["other"]["merchant"] is None
    largest = detail["insights"]["largest_expense"]
    assert (largest["amount_minor_units"], largest["currency"], largest["transaction_date"]) == (
        1000,
        "EUR",
        "2026-08-01",
    )


def test_dashboard_json_for_a_selection_required_dashboard() -> None:
    dashboard = Dashboard(
        period=AUGUST,
        currencies=(
            CurrencyTotal(currency=EUR, total_minor_units=3000, count=1),
            CurrencyTotal(currency=CurrencyCode("USD"), total_minor_units=1000, count=1),
        ),
        currency=None,
        currency_selection_required=True,
        detail=None,
    )

    body = dashboard_json(dashboard)

    assert body["currency"] is None
    assert body["currency_selection_required"] is True
    assert body["detail"] is None
    assert len(body["currencies"]) == 2  # type: ignore[arg-type]
