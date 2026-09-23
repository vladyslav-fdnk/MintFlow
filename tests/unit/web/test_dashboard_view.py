from datetime import date
from uuid import uuid4

import pytest

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
from mintflow.domain.user import Locale
from mintflow.web.dashboard import Onboarding, build_dashboard_view
from mintflow.web.formatting import display_locale
from mintflow.web.rendering import TEMPLATES
from mintflow.web.testing import Element, parse_html

EUR, PLN = CurrencyCode("EUR"), CurrencyCode("PLN")
SEPTEMBER = DashboardPeriod(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
AUGUST_TO_DATE = DashboardPeriod(date_from=date(2026, 8, 1), date_to=date(2026, 8, 23))
SEPTEMBER_TO_DATE = DashboardPeriod(date_from=date(2026, 9, 1), date_to=date(2026, 9, 23))
EN = display_locale(None)
NBSP = "\u00a0"
# CLDR puts thin spaces around the dash of a date range.
THIN = "\u2009"


def _day(day: int, total: int) -> TimeBucket:
    when = date(2026, 9, day)
    return TimeBucket(DashboardPeriod(date_from=when, date_to=when), total, 1 if total else 0)


def _comparison(change: int, basis_points: int | None) -> Comparison:
    return Comparison(
        current_period=SEPTEMBER_TO_DATE,
        current_total_minor_units=10_000 + change,
        previous_period=AUGUST_TO_DATE,
        previous_total_minor_units=10_000,
        change_minor_units=change,
        change_basis_points=basis_points,
    )


def _detail(
    *,
    count: int = 3,
    comparison: Comparison | None = None,
    merchant: str | None = "Corner Shop",
    other: bool = True,
) -> DashboardDetail:
    groceries = CategorySpending("groceries", "Groceries", 9_000, 2, 7_500)
    transport = CategorySpending("transport", "Transport", 3_000, 1, 2_500)
    return DashboardDetail(
        summary=Summary(total_minor_units=12_000, count=count, comparison=comparison),
        spending_over_time=SpendingOverTime(
            BucketGranularity.DAY, tuple(_day(day, 4_000 * (day % 2)) for day in range(1, 31))
        ),
        categories=(groceries, transport),
        top_merchants=TopMerchants(
            merchants=(MerchantSpending("Corner Shop", 9_000, 2, 7_500),),
            other=MerchantSpending(None, 3_000, 1, 2_500) if other else None,
        ),
        insights=Insights(
            largest_category=groceries,
            largest_expense=LargestExpense(
                uuid4(), Money(minor_units=6_000, currency=EUR), merchant, date(2026, 9, 12)
            ),
        ),
    )


def _dashboard(detail: DashboardDetail | None = None, **overrides: object) -> Dashboard:
    values: dict[str, object] = {
        "period": SEPTEMBER,
        "currencies": (CurrencyTotal(EUR, 12_000, 3),),
        "currency": EUR,
        "currency_selection_required": False,
        "detail": detail if detail is not None else _detail(),
    }
    values.update(overrides)
    return Dashboard(**values)  # type: ignore[arg-type]


def test_the_summary_insights_and_links_are_ready_to_show() -> None:
    view = build_dashboard_view(_dashboard(), EN)

    assert view.period == f"Sep 1{THIN}–{THIN}30, 2026"
    detail = view.detail
    assert detail is not None
    assert (detail.total, detail.count) == (f"120.00{NBSP}EUR", "3 expenses")
    assert detail.largest_category == (
        f"Your largest category was Groceries at 90.00{NBSP}EUR, representing 75% of spending."
    )
    assert detail.largest_expense == (
        f"Your largest expense was 60.00{NBSP}EUR at Corner Shop on Sep 12, 2026."
    )
    groceries = detail.categories[0]
    assert groceries.href == (
        "/expenses?date_from=2026-09-01&date_to=2026-09-30&category=groceries&currency=EUR"
    )
    assert [row.label for row in detail.merchants] == ["Corner Shop", "Other"]
    assert all(row.href is None for row in detail.merchants)


def test_a_largest_expense_without_a_merchant_reads_naturally() -> None:
    view = build_dashboard_view(_dashboard(_detail(merchant=None)), EN)

    assert view.detail is not None
    assert (
        view.detail.largest_expense == f"Your largest expense was 60.00{NBSP}EUR on Sep 12, 2026."
    )


@pytest.mark.parametrize(
    ("change", "basis_points", "expected"),
    [
        (1_500, 1_500, f"up 15% (+15.00{NBSP}EUR)"),
        (-2_500, -2_500, f"down 25% (\u221225.00{NBSP}EUR)"),
        (1_500, None, f"up +15.00{NBSP}EUR"),
        (0, 0, "no change"),
    ],
)
def test_the_comparison_names_both_windows_and_the_direction(
    change: int, basis_points: int | None, expected: str
) -> None:
    view = build_dashboard_view(
        _dashboard(_detail(comparison=_comparison(change, basis_points))), EN
    )

    assert view.detail is not None and view.detail.comparison is not None
    assert view.detail.comparison.startswith(f"Sep 1{THIN}–{THIN}23, 2026: ")
    assert expected in view.detail.comparison
    assert view.detail.comparison.endswith(f"compared with Aug 1{THIN}–{THIN}23, 2026.")


def test_several_currencies_are_listed_separately_and_never_added() -> None:
    view = build_dashboard_view(
        Dashboard(
            period=SEPTEMBER,
            currencies=(CurrencyTotal(EUR, 12_000, 3), CurrencyTotal(PLN, 50_000, 4)),
            currency=None,
            currency_selection_required=True,
            detail=None,
        ),
        EN,
    )

    assert view.selection_required and view.detail is None
    assert [(line.total, line.count) for line in view.currencies] == [
        (f"120.00{NBSP}EUR", "3 expenses"),
        (f"500.00{NBSP}PLN", "4 expenses"),
    ]
    assert view.currencies[1].href == (
        "/dashboard?date_from=2026-09-01&date_to=2026-09-30&currency=PLN"
    )
    assert view.currency_options == ("EUR", "PLN")


def test_formatting_follows_the_users_locale() -> None:
    view = build_dashboard_view(_dashboard(), display_locale(Locale("de-DE")))

    assert view.detail is not None
    assert view.detail.total == f"120,00{NBSP}EUR"
    assert view.period == "1.–30. Sept. 2026"


# --- the template -----------------------------------------------------------------------------


def _render(dashboard: Dashboard, onboarding: Onboarding | None = None) -> Element:
    html = TEMPLATES.get_template("dashboard.html").render(
        active="dashboard",
        view=build_dashboard_view(dashboard, EN),
        invalid_filters=False,
        onboarding=onboarding,
    )
    return parse_html(html)


def test_every_chart_value_is_available_as_text() -> None:
    page = _render(_dashboard())

    table = page.find("table")
    rows = table.find("tbody").find_all("tr")
    assert len(rows) == 30
    assert rows[0].text == f"Sep 1 40.00{NBSP}EUR"
    assert rows[0].find("a").attrs["href"] == (
        "/expenses?date_from=2026-09-01&date_to=2026-09-01&currency=EUR"
    )
    assert all(svg.attrs["aria-hidden"] == "true" for svg in page.find_all("svg"))
    categories = page.find("section", aria_labelledby="categories-title")
    assert [item.find("p").text for item in categories.find_all("li")] == [
        f"Groceries 90.00{NBSP}EUR · 75% · 2 expenses",
        f"Transport 30.00{NBSP}EUR · 25% · 1 expense",
    ]


def test_the_dashboard_has_no_inline_script_or_style() -> None:
    page = _render(_dashboard())

    assert all("src" in script.attrs for script in page.find_all("script"))
    assert all("style" not in element.attrs for element in page.iter())


def test_filters_are_labelled_and_keep_the_selection() -> None:
    page = _render(_dashboard())

    for field_id in ("date_from", "date_to", "currency"):
        assert page.find("label", for_=field_id)
    assert page.find("input", id="date_from").attrs["value"] == "2026-09-01"
    assert "selected" in page.find("option", value="EUR").attrs


def test_an_empty_period_says_so_instead_of_drawing_empty_charts() -> None:
    page = _render(_dashboard(_detail(count=0)))

    assert "No expenses in this period." in page.find(id="dashboard").text
    assert page.find_all("svg") == []


@pytest.mark.parametrize(
    ("onboarding", "action", "href"),
    [
        (
            Onboarding(telegram_linked=False, bot_url="https://t.me/mintflow_bot"),
            "Connect Telegram",
            "/settings",
        ),
        (
            Onboarding(telegram_linked=True, bot_url="https://t.me/mintflow_bot"),
            "Open the bot",
            "https://t.me/mintflow_bot",
        ),
    ],
)
def test_a_new_user_is_guided_to_telegram(onboarding: Onboarding, action: str, href: str) -> None:
    empty = _dashboard(_detail(count=0), currencies=())
    welcome = _render(empty, onboarding).find("section", aria_labelledby="welcome-title")

    button = welcome.find("a", class_="button")
    assert (button.text, button.attrs["href"]) == (action, href)
    assert "No expenses in this period." not in _render(empty, onboarding).text


def test_a_month_range_is_labelled_by_month() -> None:
    months = SpendingOverTime(
        BucketGranularity.MONTH,
        (
            TimeBucket(DashboardPeriod(date(2026, 7, 15), date(2026, 7, 31)), 1_000, 1),
            TimeBucket(DashboardPeriod(date(2026, 8, 1), date(2026, 8, 31)), 0, 0),
        ),
    )
    detail = _detail()
    detail = DashboardDetail(
        summary=detail.summary,
        spending_over_time=months,
        categories=detail.categories,
        top_merchants=detail.top_merchants,
        insights=detail.insights,
    )
    view = build_dashboard_view(
        _dashboard(detail, period=DashboardPeriod(date(2026, 7, 15), date(2026, 8, 31))), EN
    )

    assert view.detail is not None
    chart = view.detail.time_chart
    assert chart.title == "Spending by month"
    assert [column.label for column in chart.columns] == ["Jul 2026", "Aug 2026"]
    assert chart.summary == f"Highest: Jul 2026, 10.00{NBSP}EUR."
    assert chart.columns[0].href.startswith("/expenses?date_from=2026-07-15&date_to=2026-07-31")
