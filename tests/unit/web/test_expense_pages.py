import pytest

from mintflow.web.expense_detail import ExpenseDetail
from mintflow.web.expense_form import ORIGINAL_PREFIX, ExpenseForm
from mintflow.web.rendering import TEMPLATES
from mintflow.web.testing import Element, parse_html

DETAIL = ExpenseDetail(
    id="6f1c5a4e-0000-4000-8000-000000000001",
    amount="12.50 EUR",
    date="Sep 20, 2026",
    merchant=None,
    category="Groceries",
    note=None,
    source="Telegram",
    confirmed="Sep 20, 2026, 9:00 AM",
    changed=None,
)
FORM = ExpenseForm(
    amount="12.505",
    currency="EUR",
    transaction_date="2026-09-20",
    merchant="",
    category_key="groceries",
    note="",
)


def _edit_page(errors: dict[str, str]) -> Element:
    html = TEMPLATES.get_template("expense_edit.html").render(
        active="expenses",
        expense_id=DETAIL.id,
        form=FORM,
        original=FORM,
        original_prefix=ORIGINAL_PREFIX,
        errors=errors,
        form_error=None,
        categories=[("groceries", "Groceries")],
        currencies=("EUR", "JPY"),
    )
    return parse_html(html)


PAGES = {
    "detail": lambda: TEMPLATES.get_template("expense_detail.html").render(
        active="expenses", expense=DETAIL
    ),
    "edit": lambda: TEMPLATES.get_template("expense_edit.html").render(
        active="expenses",
        expense_id=DETAIL.id,
        form=FORM,
        original=FORM,
        original_prefix=ORIGINAL_PREFIX,
        errors={"amount": "Wrong"},
        form_error="Could not save",
        categories=[("groceries", "Groceries")],
        currencies=("EUR",),
    ),
    "delete": lambda: TEMPLATES.get_template("expense_delete.html").render(
        active="expenses", expense=DETAIL
    ),
    "deleted": lambda: TEMPLATES.get_template("expense_deleted.html").render(
        active="expenses", expense_id=DETAIL.id
    ),
}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_expense_pages_have_no_inline_script_or_style(name: str) -> None:
    page = parse_html(PAGES[name]())

    assert all("src" in script.attrs for script in page.find_all("script"))
    assert all("style" not in element.attrs for element in page.iter())


def test_every_edit_field_is_labelled_and_errors_are_linked() -> None:
    form = _edit_page({"amount": "EUR amounts have at most 2 decimal places."}).find("form")

    for name in ExpenseForm.field_names():
        control = form.find(id=name)
        assert form.find("label", for_=name).text
        assert control.attrs["name"] == name
        hidden = form.find("input", name=ORIGINAL_PREFIX + name)
        assert hidden.attrs["type"] == "hidden"
    amount = form.find("input", id="amount")
    assert amount.attrs["aria-invalid"] == "true"
    assert form.find(id=str(amount.attrs["aria-describedby"])).text.startswith("EUR amounts")
    assert "data-warn-unsaved" in form.attrs
    assert form.find("input", id="merchant").attrs["aria-describedby"] == "merchant-hint"


def test_a_merchant_error_keeps_the_hint_linked_too() -> None:
    merchant = _edit_page({"merchant": "Too long"}).find("input", id="merchant")

    assert merchant.attrs["aria-describedby"] == "merchant-hint merchant-error"
    assert merchant.attrs["aria-invalid"] == "true"
