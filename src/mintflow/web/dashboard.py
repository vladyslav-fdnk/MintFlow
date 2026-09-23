"""The dashboard page (web design W4, W7, W9; MVP section 8).

``build_dashboard_view`` turns the ``Dashboard`` snapshot into display-ready text and chart
geometry, so templates only lay things out. Amounts in different currencies are never combined:
a period with several currencies shows one total per currency and details for one of them.
"""

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from babel import Locale as BabelLocale
from fastapi import APIRouter, Request
from starlette.datastructures import QueryParams
from starlette.responses import Response

from mintflow.application.analytics import (
    BucketGranularity,
    Comparison,
    Dashboard,
    DashboardDetail,
    DashboardPeriod,
    DashboardUserNotFound,
)
from mintflow.application.capture import ExpenseHistoryFilter
from mintflow.domain.capture import CurrencyCode
from mintflow.http.analytics import BuildDashboardDependency, parse_dashboard_params
from mintflow.http.authentication import DatabaseSession
from mintflow.infrastructure.persistence import (
    SqlAlchemyExpenseRepository,
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyUserRepository,
)
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.web.charts import Column, bar_lengths, columns
from mintflow.web.formatting import (
    _,
    display_locale,
    format_amount,
    format_count,
    format_date,
    format_month,
    format_period,
    format_share,
    format_short_date,
    ngettext,
)
from mintflow.web.pages import PagePrincipalDependency, SignInRequired
from mintflow.web.rendering import render

DASHBOARD_PATH: Final = "/dashboard"
EXPENSES_PATH: Final = "/expenses"
MINUS_SIGN: Final = "\u2212"

router = APIRouter(include_in_schema=False)


# --- view model -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrencyLine:
    currency: str
    total: str
    count: str
    href: str
    selected: bool


@dataclass(frozen=True, slots=True)
class ColumnView:
    column: Column
    label: str
    amount: str
    href: str


@dataclass(frozen=True, slots=True)
class TimeChart:
    title: str
    summary: str
    columns: tuple[ColumnView, ...]
    first_label: str
    last_label: str


@dataclass(frozen=True, slots=True)
class BarRow:
    label: str
    amount: str
    share: str
    count: str
    length: float
    href: str | None


@dataclass(frozen=True, slots=True)
class Detail:
    empty: bool
    total: str
    count: str
    comparison: str | None
    time_chart: TimeChart
    categories: tuple[BarRow, ...]
    merchants: tuple[BarRow, ...]
    largest_category: str | None
    largest_expense: str | None
    largest_expense_href: str | None


@dataclass(frozen=True, slots=True)
class DashboardView:
    period: str
    date_from: str
    date_to: str
    currency: str | None
    currency_options: tuple[str, ...]
    currencies: tuple[CurrencyLine, ...]
    selection_required: bool
    detail: Detail | None


def _query(**params: str) -> str:
    return urlencode(params)


def _expenses_href(period: DashboardPeriod, currency: CurrencyCode, **extra: str) -> str:
    return f"{EXPENSES_PATH}?" + _query(
        date_from=period.date_from.isoformat(),
        date_to=period.date_to.isoformat(),
        **extra,
        currency=currency.value,
    )


def _expense_count(count: int, locale: BabelLocale) -> str:
    return ngettext(_("{count} expense"), _("{count} expenses"), count).format(
        count=format_count(count, locale)
    )


def _comparison(comparison: Comparison, currency: CurrencyCode, locale: BabelLocale) -> str:
    previous = format_period(
        comparison.previous_period.date_from, comparison.previous_period.date_to, locale
    )
    current = format_period(
        comparison.current_period.date_from, comparison.current_period.date_to, locale
    )
    change = comparison.change_minor_units
    if change == 0:
        return _("{current}: no change compared with {previous}.").format(
            current=current, previous=previous
        )
    amount = format_amount(abs(change), currency, locale)
    signed = ("+" if change > 0 else MINUS_SIGN) + amount
    change_text = (
        signed
        if comparison.change_basis_points is None
        else f"{format_share(abs(comparison.change_basis_points), locale)} ({signed})"
    )
    template = (
        _("{current}: up {change} compared with {previous}.")
        if change > 0
        else _("{current}: down {change} compared with {previous}.")
    )
    return template.format(current=current, change=change_text, previous=previous)


def _time_chart(detail: DashboardDetail, currency: CurrencyCode, locale: BabelLocale) -> TimeChart:
    over_time = detail.spending_over_time
    monthly = over_time.granularity is BucketGranularity.MONTH
    label = format_month if monthly else format_short_date
    buckets = over_time.buckets
    geometry = columns([bucket.total_minor_units for bucket in buckets])
    views = tuple(
        ColumnView(
            column=column,
            label=label(bucket.period.date_from, locale),
            amount=format_amount(bucket.total_minor_units, currency, locale),
            href=_expenses_href(bucket.period, currency),
        )
        for bucket, column in zip(buckets, geometry, strict=True)
    )
    highest = max(buckets, key=lambda bucket: bucket.total_minor_units, default=None)
    if highest is None or highest.total_minor_units == 0:
        summary = _("No spending in this period.")
    else:
        summary = _("Highest: {label}, {amount}.").format(
            label=label(highest.period.date_from, locale),
            amount=format_amount(highest.total_minor_units, currency, locale),
        )
    return TimeChart(
        title=_("Spending by month") if monthly else _("Spending by day"),
        summary=summary,
        columns=views,
        first_label=views[0].label if views else "",
        last_label=views[-1].label if views else "",
    )


def _detail(
    dashboard: Dashboard, detail: DashboardDetail, currency: CurrencyCode, locale: BabelLocale
) -> Detail:
    period = dashboard.period
    categories = detail.categories
    category_lengths = bar_lengths([category.total_minor_units for category in categories])
    merchants = [*detail.top_merchants.merchants]
    if detail.top_merchants.other is not None:
        merchants.append(detail.top_merchants.other)
    merchant_lengths = bar_lengths([merchant.total_minor_units for merchant in merchants])

    largest_category = detail.insights.largest_category
    largest_expense = detail.insights.largest_expense
    category_sentence = None
    if largest_category is not None:
        category_sentence = _(
            "Your largest category was {category} at {amount}, representing {share} of spending."
        ).format(
            category=largest_category.category_name,
            amount=format_amount(largest_category.total_minor_units, currency, locale),
            share=format_share(largest_category.share_basis_points, locale),
        )
    expense_sentence = None
    if largest_expense is not None:
        values = {
            "amount": format_amount(
                largest_expense.money.minor_units, largest_expense.money.currency, locale
            ),
            "date": format_date(largest_expense.transaction_date, locale),
        }
        expense_sentence = (
            _("Your largest expense was {amount} at {merchant} on {date}.").format(
                merchant=largest_expense.merchant, **values
            )
            if largest_expense.merchant is not None
            else _("Your largest expense was {amount} on {date}.").format(**values)
        )

    return Detail(
        empty=detail.summary.count == 0,
        total=format_amount(detail.summary.total_minor_units, currency, locale),
        count=_expense_count(detail.summary.count, locale),
        comparison=(
            _comparison(detail.summary.comparison, currency, locale)
            if detail.summary.comparison is not None
            else None
        ),
        time_chart=_time_chart(detail, currency, locale),
        categories=tuple(
            BarRow(
                label=category.category_name,
                amount=format_amount(category.total_minor_units, currency, locale),
                share=format_share(category.share_basis_points, locale),
                count=_expense_count(category.count, locale),
                length=length,
                href=_expenses_href(period, currency, category=category.category_key),
            )
            for category, length in zip(categories, category_lengths, strict=True)
        ),
        merchants=tuple(
            BarRow(
                label=merchant.merchant if merchant.merchant is not None else _("Other"),
                amount=format_amount(merchant.total_minor_units, currency, locale),
                share=format_share(merchant.share_basis_points, locale),
                count=_expense_count(merchant.count, locale),
                length=length,
                # Merchant search is postponed, so merchants do not link (dashboard design D10).
                href=None,
            )
            for merchant, length in zip(merchants, merchant_lengths, strict=True)
        ),
        largest_category=category_sentence,
        largest_expense=expense_sentence,
        largest_expense_href=(
            f"{EXPENSES_PATH}/{largest_expense.expense_id}" if largest_expense is not None else None
        ),
    )


def build_dashboard_view(dashboard: Dashboard, locale: BabelLocale) -> DashboardView:
    period = dashboard.period
    period_params = {
        "date_from": period.date_from.isoformat(),
        "date_to": period.date_to.isoformat(),
    }
    currency = dashboard.currency
    options = sorted(
        {total.currency.value for total in dashboard.currencies}
        | ({currency.value} if currency is not None else set())
    )
    lines = tuple(
        CurrencyLine(
            currency=total.currency.value,
            total=format_amount(total.total_minor_units, total.currency, locale),
            count=_expense_count(total.count, locale),
            href=f"{DASHBOARD_PATH}?" + _query(**period_params, currency=total.currency.value),
            selected=total.currency == currency,
        )
        for total in dashboard.currencies
    )
    detail = (
        _detail(dashboard, dashboard.detail, currency, locale)
        if dashboard.detail is not None and currency is not None
        else None
    )
    return DashboardView(
        period=format_period(period.date_from, period.date_to, locale),
        date_from=period_params["date_from"],
        date_to=period_params["date_to"],
        currency=currency.value if currency is not None else None,
        currency_options=tuple(options),
        currencies=lines,
        selection_required=dashboard.currency_selection_required,
        detail=detail,
    )


# --- first-use guidance -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Onboarding:
    """Shown while the user has no expenses at all (web design W9)."""

    telegram_linked: bool
    bot_url: str | None


# --- route ------------------------------------------------------------------------------------


def _filters(request: Request) -> QueryParams:
    # An emptied form field means "not set", not an invalid value.
    return QueryParams([(key, value) for key, value in request.query_params.multi_items() if value])


@router.get(DASHBOARD_PATH)
async def dashboard_page(
    request: Request,
    principal: PagePrincipalDependency,
    build_dashboard: BuildDashboardDependency,
    session: DatabaseSession,
) -> Response:
    query = parse_dashboard_params(_filters(request))
    invalid = query is None
    try:
        dashboard = build_dashboard.execute(
            caller_id=principal.user_id,
            period=query.period if query is not None else None,
            currency=query.currency if query is not None else None,
        )
    except DashboardUserNotFound:
        raise SignInRequired from None
    user = SqlAlchemyUserRepository(session).get(principal.user_id)
    if user is None:
        raise SignInRequired
    locale = display_locale(user.locale)
    onboarding = None
    has_expenses = (
        SqlAlchemyExpenseRepository(session)
        .list_history(owner_id=user.id, history_filter=ExpenseHistoryFilter(), limit=1)
        .items
    )
    if not has_expenses:
        telegram: TelegramRuntime | None = request.app.state.telegram_runtime
        connection = SqlAlchemyTelegramLinkRepository(session).active_connection_for_user(
            user_id=user.id
        )
        onboarding = Onboarding(
            telegram_linked=connection is not None,
            bot_url=f"https://t.me/{telegram.bot_username}" if telegram is not None else None,
        )
    return render(
        "dashboard.html",
        {
            "active": "dashboard",
            "view": build_dashboard_view(dashboard, locale),
            "invalid_filters": invalid,
            "onboarding": onboarding,
        },
        status_code=400 if invalid else 200,
    )
