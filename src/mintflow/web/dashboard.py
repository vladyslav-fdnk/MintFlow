"""The dashboard page (web design W4, W7, W9; MVP section 8).

``build_dashboard_view`` turns the ``Dashboard`` snapshot into display-ready text and chart
geometry, so templates only lay things out. With a default currency and exchange rates, the page
shows every currency converted into the default one by default, each converted amount marked "≈"
with a note on the rates (docs/exchange_rates_design.md, X4). Otherwise amounts in different
currencies are never combined: a period with several currencies shows one total per currency and
details for one of them. Each currency's own dashboard stays one link away.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final
from urllib.parse import urlencode

from babel import Locale as BabelLocale
from fastapi import APIRouter, Request
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams
from starlette.responses import Response

from mintflow.application.analytics import (
    BucketGranularity,
    Comparison,
    ConvertTo,
    Dashboard,
    DashboardDetail,
    DashboardPeriod,
    DashboardUserNotFound,
    local_today,
)
from mintflow.application.capture import ExpenseHistoryFilter
from mintflow.domain.capture import CurrencyCode
from mintflow.http.analytics import BuildDashboardDependency, parse_dashboard_params
from mintflow.http.authentication import DatabaseSession
from mintflow.http.capture import CaptureRuntimeDependency
from mintflow.infrastructure.persistence import (
    SqlAlchemyExchangeRateRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyUserRepository,
)
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.web.charts import Column, Slice, bar_lengths, columns, donut, nice_ceiling
from mintflow.web.formatting import (
    _,
    approximately,
    category_label,
    display_locale,
    format_amount,
    format_axis_amount,
    format_count,
    format_date,
    format_money,
    format_month,
    format_period,
    format_share,
    format_short_date,
    ngettext,
    rates_note,
    sentence,
)
from mintflow.web.pages import PagePrincipalDependency, SignInRequired, non_empty_params
from mintflow.web.rendering import render

DASHBOARD_PATH: Final = "/dashboard"
COLUMNS: Final = "columns"
PIE: Final = "pie"
CHART_VIEWS: Final = frozenset({COLUMNS, PIE})
# "?table=1" keeps the daily table open beside either chart, across every dashboard link.
TABLE_OPEN: Final = "1"
# Distinct slice colours in the stylesheet (.slice-0 .. .slice-7).
DONUT_COLORS: Final = 8
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
    # A converted dashboard leaves this currency out: it has no exchange rate.
    no_rate: bool = False


@dataclass(frozen=True, slots=True)
class ColumnView:
    column: Column
    label: str
    amount: str
    href: str

    @property
    def tooltip(self) -> str:
        return f"{self.label}: {self.amount}"


@dataclass(frozen=True, slots=True)
class TimeChart:
    title: str
    summary: str
    columns: tuple[ColumnView, ...]
    # Value axis from the top: the rounded maximum, its half, and zero.
    axis: tuple[str, str, str]
    # Evenly spaced date labels under the columns.
    ticks: tuple[str, ...]
    # The table beside the chart lists only the buckets with spending.
    rows: tuple[ColumnView, ...] = ()


@dataclass(frozen=True, slots=True)
class DonutSlice:
    label: str
    amount: str
    share: str
    href: str
    color: int
    slice: Slice


@dataclass(frozen=True, slots=True)
class ChartLink:
    label: str
    href: str
    selected: bool


@dataclass(frozen=True, slots=True)
class Preset:
    label: str
    href: str
    selected: bool
    # The dates the preset stands for, shown under its label.
    dates: str = ""


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
    donut: tuple[DonutSlice, ...]
    categories: tuple[BarRow, ...]
    merchants: tuple[BarRow, ...]
    largest_category: str | None
    largest_expense: str | None
    largest_expense_href: str | None
    # "up", "down", or "flat" when there is a comparison.
    comparison_direction: str | None = None


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
    chart: str
    chart_links: tuple[ChartLink, ...]
    presets: tuple[Preset, ...]
    # Whether the daily table is open, and the link that opens or closes it.
    table: bool = False
    table_href: str = ""
    # The currency everything can be converted into, when there is a default and rates.
    main_currency: str | None = None
    # The currency menu's first choice: converted into the main currency, or automatic.
    all_label: str = ""
    converted: bool = False
    converted_href: str | None = None
    show_currency_choice: bool = False
    rates_note: str | None = None
    rates_detail: str | None = None
    unconverted_note: str | None = None
    # Several currencies shown one at a time: why, and where to change it.
    needs_default_currency: bool = False
    rates_missing: bool = False


def _query(**params: str) -> str:
    return urlencode(params)


def _expenses_href(period: DashboardPeriod, currency: CurrencyCode | None, **extra: str) -> str:
    """The matching history; a converted dashboard links to every currency."""
    currency_param = {"currency": currency.value} if currency is not None else {}
    return f"{EXPENSES_PATH}?" + _query(
        date_from=period.date_from.isoformat(),
        date_to=period.date_to.isoformat(),
        **extra,
        **currency_param,
    )


@dataclass(frozen=True, slots=True)
class _Amounts:
    """Formats one dashboard's amounts, marking them "≈" when they were converted."""

    currency: CurrencyCode
    locale: BabelLocale
    approximate: bool

    def mark(self, text: str) -> str:
        return approximately(text) if self.approximate else text

    def amount(self, minor_units: int) -> str:
        return self.mark(format_amount(minor_units, self.currency, self.locale))

    def axis(self, minor_units: int) -> str:
        return self.mark(format_axis_amount(minor_units, self.currency, self.locale))


def _expense_count(count: int, locale: BabelLocale) -> str:
    return ngettext("{count} expense", "{count} expenses", count).format(
        count=format_count(count, locale)
    )


def _comparison(comparison: Comparison, amounts: _Amounts) -> str:
    """The change against the previous comparable window; the window itself implies the days."""
    locale = amounts.locale
    previous = format_period(
        comparison.previous_period.date_from, comparison.previous_period.date_to, locale
    )
    change = comparison.change_minor_units
    if change == 0:
        return sentence(_("No change compared with {previous}.").format(previous=previous))
    signed = amounts.mark(
        ("+" if change > 0 else MINUS_SIGN) + format_amount(abs(change), amounts.currency, locale)
    )
    change_text = (
        signed
        if comparison.change_basis_points is None
        else f"{format_share(abs(comparison.change_basis_points), locale)} ({signed})"
    )
    template = (
        _("Up {change} compared with {previous}.")
        if change > 0
        else _("Down {change} compared with {previous}.")
    )
    return sentence(template.format(change=change_text, previous=previous))


def _direction(comparison: Comparison) -> str:
    change = comparison.change_minor_units
    return "up" if change > 0 else "down" if change < 0 else "flat"


def _time_chart(
    detail: DashboardDetail, amounts: _Amounts, link_currency: CurrencyCode | None
) -> TimeChart:
    locale = amounts.locale
    over_time = detail.spending_over_time
    monthly = over_time.granularity is BucketGranularity.MONTH
    label = format_month if monthly else format_short_date
    buckets = over_time.buckets
    top = nice_ceiling(max((bucket.total_minor_units for bucket in buckets), default=0))
    geometry = columns([bucket.total_minor_units for bucket in buckets], maximum=top or None)
    views = tuple(
        ColumnView(
            column=column,
            label=label(bucket.period.date_from, locale),
            amount=amounts.amount(bucket.total_minor_units),
            href=_expenses_href(bucket.period, link_currency),
        )
        for bucket, column in zip(buckets, geometry, strict=True)
    )
    highest = max(buckets, key=lambda bucket: bucket.total_minor_units, default=None)
    if highest is None or highest.total_minor_units == 0:
        summary = _("No spending in this period.")
    else:
        summary = _("Highest: {label}, {amount}.").format(
            label=label(highest.period.date_from, locale),
            amount=amounts.amount(highest.total_minor_units),
        )
    last = len(views) - 1
    tick_indexes = sorted({0, last // 4, last // 2, 3 * last // 4, last}) if views else []
    return TimeChart(
        title=_("Spending by month") if monthly else _("Spending by day"),
        summary=summary,
        columns=views,
        # Zero is exact, so only the two upper labels carry the "≈" mark.
        axis=(
            amounts.axis(top),
            amounts.axis(top // 2),
            format_axis_amount(0, amounts.currency, locale),
        ),
        ticks=tuple(views[index].label for index in tick_indexes),
        rows=tuple(view for bucket, view in zip(buckets, views, strict=True) if bucket.count),
    )


def _largest_expense_amount(detail: DashboardDetail, amounts: _Amounts) -> str | None:
    """The expense's own amount, followed by the converted one when it is in another currency."""
    largest = detail.insights.largest_expense
    if largest is None:
        return None
    own = format_money(largest.money, amounts.locale)
    if largest.converted_minor_units is None or largest.money.currency == amounts.currency:
        return own
    converted = approximately(
        format_amount(largest.converted_minor_units, amounts.currency, amounts.locale)
    )
    return f"{own} ({converted})"


def _detail(
    dashboard: Dashboard,
    detail: DashboardDetail,
    amounts: _Amounts,
    link_currency: CurrencyCode | None,
) -> Detail:
    locale = amounts.locale
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
            category=category_label(largest_category.category_key, largest_category.category_name),
            amount=amounts.amount(largest_category.total_minor_units),
            share=format_share(largest_category.share_basis_points, locale),
        )
    expense_sentence = None
    if largest_expense is not None:
        values = {
            "amount": _largest_expense_amount(detail, amounts),
            "date": format_date(largest_expense.transaction_date, locale),
        }
        expense_sentence = sentence(
            _("Your largest expense was {amount} at {merchant} on {date}.").format(
                merchant=largest_expense.merchant, **values
            )
            if largest_expense.merchant is not None
            else _("Your largest expense was {amount} on {date}.").format(**values)
        )

    slices = donut([category.share_basis_points for category in categories])
    donut_view = tuple(
        DonutSlice(
            label=category_label(category.category_key, category.category_name),
            amount=amounts.amount(category.total_minor_units),
            share=format_share(category.share_basis_points, locale),
            href=_expenses_href(period, link_currency, category=category.category_key),
            color=index % DONUT_COLORS,
            slice=slice_,
        )
        for index, (category, slice_) in enumerate(zip(categories, slices, strict=True))
    )
    return Detail(
        empty=detail.summary.count == 0,
        donut=donut_view,
        total=amounts.amount(detail.summary.total_minor_units),
        count=_expense_count(detail.summary.count, locale),
        comparison=(
            _comparison(detail.summary.comparison, amounts)
            if detail.summary.comparison is not None
            else None
        ),
        time_chart=_time_chart(detail, amounts, link_currency),
        categories=tuple(
            BarRow(
                label=category_label(category.category_key, category.category_name),
                amount=amounts.amount(category.total_minor_units),
                share=format_share(category.share_basis_points, locale),
                count=_expense_count(category.count, locale),
                length=length,
                href=_expenses_href(period, link_currency, category=category.category_key),
            )
            for category, length in zip(categories, category_lengths, strict=True)
        ),
        merchants=tuple(
            BarRow(
                label=merchant.merchant if merchant.merchant is not None else _("Other"),
                amount=amounts.amount(merchant.total_minor_units),
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
        comparison_direction=(
            _direction(detail.summary.comparison) if detail.summary.comparison is not None else None
        ),
    )


def _presets(
    today: date, current: DashboardPeriod, extra: dict[str, str], locale: BabelLocale
) -> tuple[Preset, ...]:
    """Today, this week (Monday to Sunday), this month, last month, and the last three months,
    in the user's calendar."""
    first_this = today.replace(day=1)
    last_this = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    last_previous = first_this - timedelta(days=1)
    first_previous = last_previous.replace(day=1)
    first_three = (first_previous - timedelta(days=1)).replace(day=1)
    monday = today - timedelta(days=today.weekday())
    choices = (
        (_("Today"), today, today),
        (_("This week"), monday, monday + timedelta(days=6)),
        (_("This month"), first_this, last_this),
        (_("Last month"), first_previous, last_previous),
        (_("Last 3 months"), first_three, last_this),
    )
    return tuple(
        Preset(
            label=label,
            href=f"{DASHBOARD_PATH}?"
            + _query(date_from=start.isoformat(), date_to=end.isoformat(), **extra),
            selected=(start, end) == (current.date_from, current.date_to),
            dates=format_period(start, end, locale),
        )
        for label, start, end in choices
    )


def build_dashboard_view(
    dashboard: Dashboard,
    locale: BabelLocale,
    *,
    chart: str = COLUMNS,
    table: bool = False,
    today: date | None = None,
    default_currency: CurrencyCode | None = None,
    rates_available: bool = False,
) -> DashboardView:
    """``default_currency`` and ``rates_available`` describe the user's conversion setup, which
    also applies when ``dashboard`` is one currency's own view."""
    period = dashboard.period
    period_params = {
        "date_from": period.date_from.isoformat(),
        "date_to": period.date_to.isoformat(),
    }
    # The page's own choices, carried by every link so switching one keeps the others.
    view_params = {"chart": chart, **({"table": TABLE_OPEN} if table else {})}
    currency = dashboard.currency
    conversion = dashboard.conversion
    main_currency = default_currency if rates_available else None
    unconverted = (
        {total.currency for total in conversion.unconverted} if conversion is not None else set()
    )
    options = sorted(
        {total.currency.value for total in dashboard.currencies}
        | ({currency.value} if currency is not None and conversion is None else set())
    )
    lines = tuple(
        CurrencyLine(
            currency=total.currency.value,
            total=format_amount(total.total_minor_units, total.currency, locale),
            count=_expense_count(total.count, locale),
            href=f"{DASHBOARD_PATH}?"
            + _query(**period_params, currency=total.currency.value, **view_params),
            selected=conversion is None and total.currency == currency,
            no_rate=total.currency in unconverted,
        )
        for total in dashboard.currencies
    )
    approximate = conversion is not None and bool(conversion.rates)
    detail = (
        _detail(
            dashboard,
            dashboard.detail,
            _Amounts(currency=currency, locale=locale, approximate=approximate),
            link_currency=None if conversion is not None else currency,
        )
        if dashboard.detail is not None and currency is not None
        else None
    )
    currency_param = (
        {"currency": currency.value} if currency is not None and conversion is None else {}
    )
    chart_links = tuple(
        ChartLink(
            label=label,
            href=f"{DASHBOARD_PATH}?"
            + _query(**period_params, **currency_param, **{**view_params, "chart": kind}),
            selected=kind == chart,
        )
        for kind, label in ((COLUMNS, _("Columns")), (PIE, _("Pie")))
    )
    several = len(dashboard.currencies) > 1
    return DashboardView(
        chart=chart,
        chart_links=chart_links,
        table=table,
        table_href=f"{DASHBOARD_PATH}?"
        + _query(
            **period_params,
            **currency_param,
            chart=chart,
            **({} if table else {"table": TABLE_OPEN}),
        ),
        presets=(
            _presets(today, period, {**currency_param, **view_params}, locale)
            if today is not None
            else ()
        ),
        period=format_period(period.date_from, period.date_to, locale),
        date_from=period_params["date_from"],
        date_to=period_params["date_to"],
        currency=currency.value if currency is not None else None,
        currency_options=tuple(options),
        currencies=lines,
        selection_required=dashboard.currency_selection_required,
        detail=detail,
        main_currency=main_currency.value if main_currency is not None else None,
        all_label=(
            _("All in {currency}").format(currency=main_currency.value)
            if main_currency is not None
            else _("Automatic")
        ),
        converted=conversion is not None,
        converted_href=(
            f"{DASHBOARD_PATH}?" + _query(**period_params, **view_params)
            if main_currency is not None
            else None
        ),
        show_currency_choice=several
        or (
            main_currency is not None
            and any(t.currency != main_currency for t in dashboard.currencies)
        ),
        rates_note=(
            rates_note(conversion.rates, currency, locale)
            if conversion is not None and conversion.rates and currency is not None
            else None
        ),
        rates_detail=_(
            "Past spending is converted at today's rates, so these totals can change slightly."
        ),
        unconverted_note=(
            sentence(
                _("Not included, no exchange rate: {amounts}.").format(
                    amounts=", ".join(
                        format_amount(total.total_minor_units, total.currency, locale)
                        for total in conversion.unconverted
                    )
                )
            )
            if conversion is not None and conversion.unconverted
            else None
        ),
        needs_default_currency=several and default_currency is None,
        rates_missing=several and default_currency is not None and not rates_available,
    )


# --- first-use guidance -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Onboarding:
    """Shown while the user has no expenses at all (web design W9)."""

    telegram_linked: bool
    bot_url: str | None


# --- route ------------------------------------------------------------------------------------


def main_currency(session: Session, default_currency: CurrencyCode | None) -> ConvertTo | None:
    """The conversion into the default currency, when there is one and it has a rate."""
    if default_currency is None:
        return None
    rates = SqlAlchemyExchangeRateRepository(session).latest()
    if not rates.rates or not rates.has_rate(default_currency):
        return None
    return ConvertTo(default_currency, rates)


@router.get(DASHBOARD_PATH)
async def dashboard_page(
    request: Request,
    principal: PagePrincipalDependency,
    build_dashboard: BuildDashboardDependency,
    session: DatabaseSession,
    runtime: CaptureRuntimeDependency,
) -> Response:
    params = non_empty_params(request)
    # The chart view and the table are the page's own choices, not dashboard query parameters.
    chart = params.get("chart", COLUMNS)
    chart = chart if chart in CHART_VIEWS else COLUMNS
    table = params.get("table") == TABLE_OPEN
    query = parse_dashboard_params(
        QueryParams(
            [(key, value) for key, value in params.multi_items() if key not in {"chart", "table"}]
        )
    )
    invalid = query is None
    user = SqlAlchemyUserRepository(session).get(principal.user_id)
    if user is None:
        raise SignInRequired
    requested = query.currency if query is not None else None
    main = main_currency(session, user.default_currency)
    try:
        dashboard = build_dashboard.execute(
            caller_id=principal.user_id,
            period=query.period if query is not None else None,
            currency=requested,
            # A chosen currency shows its own dashboard; otherwise everything converts.
            convert_to=main if requested is None else None,
        )
    except DashboardUserNotFound:
        raise SignInRequired from None
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
            "view": build_dashboard_view(
                dashboard,
                locale,
                chart=chart,
                table=table,
                today=local_today(now=runtime.clock(), timezone=user.timezone),
                default_currency=user.default_currency,
                rates_available=main is not None,
            ),
            "invalid_filters": invalid,
            "onboarding": onboarding,
        },
        status_code=400 if invalid else 200,
    )
