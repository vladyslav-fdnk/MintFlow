from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from mintflow.application.capture import ExpenseHistoryFilter, ExpenseHistoryPage
from mintflow.application.capture.expense_history import (
    ExpenseHistoryPosition,
    decode_history_cursor,
)
from mintflow.domain.capture import (
    CaptureSource,
    Category,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.web.expenses import build_history_view
from mintflow.web.formatting import display_locale
from mintflow.web.rendering import TEMPLATES
from mintflow.web.testing import parse_html

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
EN = display_locale(None)
NBSP = "\u00a0"
CATEGORIES = [Category(id=uuid4(), key="groceries", name="Groceries", is_active=True)]


def _expense(merchant: str | None, category: str = "groceries") -> Expense:
    return Expense.create(
        owner_id=uuid4(),
        money=Money(minor_units=1_250, currency=CurrencyCode("EUR")),
        transaction_date=TransactionDate(date(2026, 9, 20)),
        category_key=category,
        capture_draft_id=uuid4(),
        merchant=MerchantName(merchant) if merchant is not None else None,
        source=CaptureSource.TELEGRAM_MANUAL,
        now=NOW,
    )


POSITION = ExpenseHistoryPosition(
    transaction_date=date(2026, 9, 20), created_at=NOW, expense_id=uuid4()
)
FILTER = ExpenseHistoryFilter(
    date_from=date(2026, 9, 1),
    date_to=date(2026, 9, 30),
    category_keys=frozenset({"groceries"}),
    currencies=frozenset({CurrencyCode("EUR")}),
)


def test_rows_are_formatted_and_link_to_their_expense() -> None:
    expense = _expense("Corner Shop")
    view = build_history_view(
        ExpenseHistoryPage(items=(expense,), next_position=None),
        ExpenseHistoryFilter(),
        categories=CATEGORIES,
        locale=EN,
        continued=False,
    )

    [row] = view.rows
    assert (row.href, row.date, row.merchant, row.category, row.amount) == (
        f"/expenses/{expense.id}",
        "Sep 20, 2026",
        "Corner Shop",
        "Groceries",
        f"12.50{NBSP}EUR",
    )
    assert view.next_href is None and not view.filtered


def test_an_inactive_category_shows_its_key() -> None:
    view = build_history_view(
        ExpenseHistoryPage(items=(_expense(None, category="retired"),), next_position=None),
        ExpenseHistoryFilter(),
        categories=CATEGORIES,
        locale=EN,
        continued=False,
    )

    assert (view.rows[0].category, view.rows[0].merchant) == ("retired", None)


def test_load_more_keeps_every_filter_and_adds_the_cursor() -> None:
    view = build_history_view(
        ExpenseHistoryPage(items=(_expense("Kiosk"),), next_position=POSITION),
        FILTER,
        categories=CATEGORIES,
        locale=EN,
        continued=False,
    )

    assert view.next_href is not None
    link = urlsplit(view.next_href)
    params = parse_qs(link.query)
    assert link.path == "/expenses"
    assert {name: values for name, values in params.items() if name != "cursor"} == {
        "date_from": ["2026-09-01"],
        "date_to": ["2026-09-30"],
        "category": ["groceries"],
        "currency": ["EUR"],
    }
    assert decode_history_cursor(params["cursor"][0]) == POSITION
    assert (view.category, view.currency, view.date_from) == ("groceries", "EUR", "2026-09-01")


def _render(**view_options: object) -> str:
    view = build_history_view(
        ExpenseHistoryPage(items=(_expense("Kiosk"), _expense(None)), next_position=POSITION),
        FILTER,
        categories=CATEGORIES,
        locale=EN,
        continued=False,
    )
    return TEMPLATES.get_template("expenses.html").render(
        active="expenses", view=view, invalid_filters=False, **view_options
    )


def test_the_table_is_accessible_and_load_more_appends_rows() -> None:
    page = parse_html(_render())

    headers = page.find("thead").find_all("th")
    assert [(header.text, header.attrs["scope"]) for header in headers] == [
        ("Date", "col"),
        ("Merchant", "col"),
        ("Category", "col"),
        ("Amount", "col"),
    ]
    rows = page.find("tbody", id="expense-rows").find_all("tr")
    assert "data-page-first" in rows[0].attrs and "data-page-first" not in rows[1].attrs
    assert rows[1].find_all("td")[1].text == "No merchant"
    more = page.find("div", id="load-more").find("a")
    assert more.attrs["hx-target"] == "#expense-rows"
    assert more.attrs["hx-swap"] == "beforeend"
    assert more.attrs["hx-select-oob"] == "#load-more"
    assert more.attrs["href"] == more.attrs["hx-get"]
    for label_for in ("date_from", "date_to", "category", "currency"):
        assert page.find("label", for_=label_for)


def test_the_history_page_has_no_inline_script_or_style() -> None:
    page = parse_html(_render())

    assert all("src" in script.attrs for script in page.find_all("script"))
    assert all("style" not in element.attrs for element in page.iter())
