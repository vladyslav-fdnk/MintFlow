"""Expense detail, edit, delete, and restore (web design W2, W3, W6; MVP section 6).

Every route looks the Expense up by id and owner, so another user's Expense, a malformed id, and
a missing one are the same 404. Mutations are htmx requests carrying the CSRF header; on success
they answer with ``HX-Redirect`` so the browser lands on a normal page.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from babel import Locale as BabelLocale
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from mintflow.application.capture import (
    ExpenseEditRejected,
    ExpenseNotFound,
    is_within_future_tolerance,
)
from mintflow.domain.capture import CaptureSource, Category, Expense, supported_currency_codes
from mintflow.domain.user import User
from mintflow.http.authentication import DatabaseSession
from mintflow.http.capture import (
    CaptureRuntimeDependency,
    DeleteExpenseDependency,
    EditExpenseDependency,
    RestoreExpenseDependency,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyUserRepository,
)
from mintflow.web.expense_form import (
    ORIGINAL_PREFIX,
    ExpenseForm,
    form_from_expense,
    validate_edit,
)
from mintflow.web.formatting import (
    N_,
    _,
    category_label,
    display_locale,
    format_date,
    format_datetime,
    format_money,
)
from mintflow.web.forms import read_form
from mintflow.web.pages import (
    PagePrincipalDependency,
    SignInRequired,
    WebMutationPrincipalDependency,
)
from mintflow.web.rendering import htmx_redirect, render

EXPENSES_PATH: Final = "/expenses"
# Two copies of every field (current and original), a long note included.
MAX_EDIT_BODY_BYTES: Final = 64 * 1024

router = APIRouter(include_in_schema=False)

_SOURCES: Final = {
    CaptureSource.TELEGRAM_MANUAL: N_("Telegram"),
    CaptureSource.TELEGRAM_RECEIPT: N_("Telegram receipt"),
    CaptureSource.WEB_MANUAL: N_("Web"),
}
_STATUS_MESSAGES: Final = {
    "saved": N_("Changes saved."),
    "restored": N_("Expense restored."),
}


@dataclass(frozen=True, slots=True)
class ExpenseDetail:
    id: str
    amount: str
    date: str
    merchant: str | None
    category: str
    note: str | None
    source: str
    confirmed: str
    changed: str | None


def _not_found() -> HTTPException:
    return HTTPException(status_code=404)


def _expense_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise _not_found() from None


def _user(session: DatabaseSession, user_id: UUID) -> User:
    user = SqlAlchemyUserRepository(session).get(user_id)
    if user is None:
        raise SignInRequired
    return user


def _active(session: DatabaseSession, expense_id: UUID, owner_id: UUID) -> Expense:
    expense = SqlAlchemyExpenseRepository(session).get_active(
        expense_id=expense_id, owner_id=owner_id
    )
    if expense is None:
        raise _not_found()
    return expense


def _categories(session: DatabaseSession) -> list[Category]:
    return SqlAlchemyCategoryRepository(session).list_active()


def build_detail(
    expense: Expense, *, user: User, categories: list[Category], locale: BabelLocale
) -> ExpenseDetail:
    names = {category.key: category_label(category.key, category.name) for category in categories}
    changed = expense.modified_at != expense.created_at
    return ExpenseDetail(
        id=str(expense.id),
        amount=format_money(expense.money, locale),
        date=format_date(expense.transaction_date.value, locale),
        merchant=expense.merchant.value if expense.merchant is not None else None,
        category=names.get(expense.category_key, expense.category_key),
        note=expense.note,
        source=_(_SOURCES.get(expense.source, expense.source.value)),
        confirmed=format_datetime(expense.created_at, user.timezone, locale),
        changed=format_datetime(expense.modified_at, user.timezone, locale) if changed else None,
    )


def _edit_page(
    expense: Expense,
    *,
    form: ExpenseForm,
    original: ExpenseForm,
    categories: list[Category],
    errors: dict[str, str],
    form_error: str | None = None,
    status_code: int = 200,
) -> Response:
    return render(
        "expense_edit.html",
        {
            "active": "expenses",
            "expense_id": str(expense.id),
            "form": form,
            "original": original,
            "original_prefix": ORIGINAL_PREFIX,
            "errors": errors,
            "form_error": form_error,
            "categories": [
                (category.key, category_label(category.key, category.name))
                for category in categories
            ],
            "currencies": supported_currency_codes(),
        },
        status_code=status_code,
    )


@router.get(EXPENSES_PATH + "/{raw_id}")
async def expense_page(
    raw_id: str, request: Request, principal: PagePrincipalDependency, session: DatabaseSession
) -> Response:
    user = _user(session, principal.user_id)
    expense = _active(session, _expense_id(raw_id), user.id)
    detail = build_detail(
        expense, user=user, categories=_categories(session), locale=display_locale(user.locale)
    )
    message = _STATUS_MESSAGES.get(request.query_params.get("done", ""))
    return render(
        "expense_detail.html",
        {"active": "expenses", "expense": detail, "status_message": _(message) if message else ""},
    )


@router.get(EXPENSES_PATH + "/{raw_id}/edit")
async def edit_page(
    raw_id: str, principal: PagePrincipalDependency, session: DatabaseSession
) -> Response:
    user = _user(session, principal.user_id)
    expense = _active(session, _expense_id(raw_id), user.id)
    form = form_from_expense(expense, display_locale(user.locale))
    return _edit_page(expense, form=form, original=form, categories=_categories(session), errors={})


@router.post(EXPENSES_PATH + "/{raw_id}/edit")
async def edit_expense(
    raw_id: str,
    request: Request,
    principal: WebMutationPrincipalDependency,
    session: DatabaseSession,
    edit_use_case: EditExpenseDependency,
    runtime: CaptureRuntimeDependency,
) -> Response:
    user = _user(session, principal.user_id)
    expense = _active(session, _expense_id(raw_id), user.id)
    locale = display_locale(user.locale)
    categories = _categories(session)
    names = ExpenseForm.field_names()
    values = await read_form(
        request,
        fields={*names, *(ORIGINAL_PREFIX + name for name in names)},
        max_bytes=MAX_EDIT_BODY_BYTES,
    )
    submitted = ExpenseForm.from_mapping(values) if values is not None else None
    original = (
        ExpenseForm.from_mapping(values, prefix=ORIGINAL_PREFIX) if values is not None else None
    )
    if submitted is None or original is None:
        current = form_from_expense(expense, locale)
        return _edit_page(
            expense,
            form=current,
            original=current,
            categories=categories,
            errors={},
            form_error=_("The form could not be read. Please try again."),
            status_code=422,
        )
    validated = validate_edit(
        submitted,
        original,
        locale=locale,
        category_keys={category.key for category in categories},
        date_is_acceptable=lambda value: is_within_future_tolerance(
            value, now=runtime.clock(), timezone=user.timezone
        ),
    )
    if validated.errors:
        return _edit_page(
            expense,
            form=submitted,
            original=original,
            categories=categories,
            errors=validated.errors,
            status_code=422,
        )
    if not validated.changes_anything:
        return htmx_redirect(f"{EXPENSES_PATH}/{expense.id}")
    try:
        edit_use_case.execute(expense_id=expense.id, caller_id=user.id, edit=validated.edit)
    except ExpenseNotFound:
        raise _not_found() from None
    except ExpenseEditRejected:
        return _edit_page(
            expense,
            form=submitted,
            original=original,
            categories=categories,
            errors={},
            form_error=_("These values could not be saved. Please check them and try again."),
            status_code=422,
        )
    return htmx_redirect(f"{EXPENSES_PATH}/{expense.id}?done=saved")


@router.get(EXPENSES_PATH + "/{raw_id}/delete")
async def delete_page(
    raw_id: str, principal: PagePrincipalDependency, session: DatabaseSession
) -> Response:
    user = _user(session, principal.user_id)
    expense = _active(session, _expense_id(raw_id), user.id)
    detail = build_detail(
        expense, user=user, categories=_categories(session), locale=display_locale(user.locale)
    )
    return render("expense_delete.html", {"active": "expenses", "expense": detail})


@router.post(EXPENSES_PATH + "/{raw_id}/delete")
async def delete_expense(
    raw_id: str,
    principal: WebMutationPrincipalDependency,
    delete_use_case: DeleteExpenseDependency,
) -> Response:
    expense_id = _expense_id(raw_id)
    try:
        delete_use_case.execute(expense_id=expense_id, caller_id=principal.user_id)
    except ExpenseNotFound:
        raise _not_found() from None
    return htmx_redirect(f"{EXPENSES_PATH}/{expense_id}/deleted")


@router.get(EXPENSES_PATH + "/{raw_id}/deleted")
async def deleted_page(
    raw_id: str, principal: PagePrincipalDependency, session: DatabaseSession
) -> Response:
    expense = SqlAlchemyExpenseRepository(session).get(
        expense_id=_expense_id(raw_id), owner_id=principal.user_id
    )
    if expense is None or expense.is_active:
        raise _not_found()
    return render("expense_deleted.html", {"active": "expenses", "expense_id": str(expense.id)})


@router.post(EXPENSES_PATH + "/{raw_id}/restore")
async def restore_expense(
    raw_id: str,
    principal: WebMutationPrincipalDependency,
    restore_use_case: RestoreExpenseDependency,
) -> Response:
    expense_id = _expense_id(raw_id)
    try:
        restore_use_case.execute(expense_id=expense_id, caller_id=principal.user_id)
    except ExpenseNotFound:
        raise _not_found() from None
    return htmx_redirect(f"{EXPENSES_PATH}/{expense_id}?done=restored")
