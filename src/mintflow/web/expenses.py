"""The expense history page (web design W1, W6; MVP section 6).

It uses the same history query and filter rules as ``GET /capture/expenses``: confirmed,
non-deleted Expenses, newest transaction date first, keyset pages. "Load more" appends the next
page with htmx; without JavaScript the same link opens the next page on its own.
"""

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from babel import Locale as BabelLocale
from fastapi import APIRouter, Request
from starlette.responses import Response

from mintflow.application.capture import (
    ExpenseHistoryFilter,
    ExpenseHistoryPage,
    encode_history_cursor,
)
from mintflow.domain.capture import Category, Expense, supported_currency_codes
from mintflow.http.authentication import DatabaseSession
from mintflow.http.capture import parse_expense_history_params
from mintflow.infrastructure.persistence import (
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyUserRepository,
)
from mintflow.web.formatting import display_locale, format_date, format_money
from mintflow.web.pages import PagePrincipalDependency, SignInRequired, non_empty_params
from mintflow.web.rendering import render

EXPENSES_PATH: Final = "/expenses"
PAGE_SIZE: Final = 50

router = APIRouter(include_in_schema=False)


@dataclass(frozen=True, slots=True)
class ExpenseRow:
    href: str
    date: str
    merchant: str | None
    category: str
    amount: str


@dataclass(frozen=True, slots=True)
class HistoryView:
    rows: tuple[ExpenseRow, ...]
    next_href: str | None
    # Filter values as the form shows them.
    date_from: str
    date_to: str
    category: str
    currency: str
    category_options: tuple[tuple[str, str], ...]
    currency_options: tuple[str, ...]
    filtered: bool
    # True on a page reached with a cursor, i.e. not the first page.
    continued: bool


def _row(expense: Expense, category_names: dict[str, str], locale: BabelLocale) -> ExpenseRow:
    return ExpenseRow(
        href=f"{EXPENSES_PATH}/{expense.id}",
        date=format_date(expense.transaction_date.value, locale),
        merchant=expense.merchant.value if expense.merchant is not None else None,
        category=category_names.get(expense.category_key, expense.category_key),
        amount=format_money(expense.money, locale),
    )


def _single(values: frozenset[str]) -> str:
    return next(iter(values)) if len(values) == 1 else ""


def build_history_view(
    page: ExpenseHistoryPage,
    history_filter: ExpenseHistoryFilter,
    *,
    categories: list[Category],
    locale: BabelLocale,
    continued: bool,
) -> HistoryView:
    names = {category.key: category.name for category in categories}
    filter_params: list[tuple[str, str]] = []
    if history_filter.date_from is not None:
        filter_params.append(("date_from", history_filter.date_from.isoformat()))
    if history_filter.date_to is not None:
        filter_params.append(("date_to", history_filter.date_to.isoformat()))
    filter_params += [("category", key) for key in sorted(history_filter.category_keys)]
    filter_params += [
        ("currency", currency.value) for currency in sorted(history_filter.currencies, key=str)
    ]
    next_href = None
    if page.next_position is not None:
        cursor = encode_history_cursor(page.next_position)
        next_href = f"{EXPENSES_PATH}?" + urlencode([*filter_params, ("cursor", cursor)])
    return HistoryView(
        rows=tuple(_row(expense, names, locale) for expense in page.items),
        next_href=next_href,
        date_from=(
            history_filter.date_from.isoformat() if history_filter.date_from is not None else ""
        ),
        date_to=history_filter.date_to.isoformat() if history_filter.date_to is not None else "",
        category=_single(history_filter.category_keys),
        currency=_single(frozenset(currency.value for currency in history_filter.currencies)),
        category_options=tuple((category.key, category.name) for category in categories),
        currency_options=supported_currency_codes(),
        filtered=bool(filter_params),
        continued=continued,
    )


@router.get(EXPENSES_PATH)
async def expense_history_page(
    request: Request, principal: PagePrincipalDependency, session: DatabaseSession
) -> Response:
    params = non_empty_params(request)
    # The page size is fixed here; only the JSON API lets a client choose it.
    query = parse_expense_history_params(params) if "limit" not in params else None
    invalid = query is None
    history_filter = query.history_filter if query is not None else ExpenseHistoryFilter()
    user = SqlAlchemyUserRepository(session).get(principal.user_id)
    if user is None:
        raise SignInRequired
    page = SqlAlchemyExpenseRepository(session).list_history(
        owner_id=user.id,
        history_filter=history_filter,
        limit=PAGE_SIZE,
        after=query.after if query is not None else None,
    )
    view = build_history_view(
        page,
        history_filter,
        categories=SqlAlchemyCategoryRepository(session).list_active(),
        locale=display_locale(user.locale),
        continued=query is not None and query.after is not None,
    )
    return render(
        "expenses.html",
        {"active": "expenses", "view": view, "invalid_filters": invalid},
        status_code=400 if invalid else 200,
    )
