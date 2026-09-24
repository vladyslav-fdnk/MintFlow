"""Deleting the account from Settings (docs/account_deletion_design.md, A3, A4).

The page explains what goes and that it cannot be undone, and the user confirms by typing the
account's email. The POST is a Web mutation, so it needs a live session and the CSRF header. A
mismatch deletes nothing. Success clears the session cookies and leads to a public page.
"""

from typing import Final

from fastapi import APIRouter, Request
from starlette.responses import Response

from mintflow.application.users import ConfirmAndDeleteAccount, DeleteAccount, DeletionNotConfirmed
from mintflow.http.authentication import DatabaseSession, clear_authentication_cookies
from mintflow.infrastructure.persistence import PostgreSQLAccountDeletionRepository
from mintflow.web.formatting import _
from mintflow.web.forms import read_form
from mintflow.web.pages import PagePrincipalDependency, WebMutationPrincipalDependency
from mintflow.web.rendering import htmx_redirect, render

DELETE_ACCOUNT_PATH: Final = "/settings/delete-account"
ACCOUNT_DELETED_PATH: Final = "/account-deleted"
MAX_DELETION_BODY_BYTES: Final = 1_024

router = APIRouter(include_in_schema=False)


def _page(*, email: str = "", error: str | None = None, status_code: int = 200) -> Response:
    return render(
        "account_delete.html",
        {"active": "settings", "email": email, "error": error},
        status_code=status_code,
    )


@router.get(DELETE_ACCOUNT_PATH)
async def delete_account_page(principal: PagePrincipalDependency) -> Response:
    return _page()


@router.post(DELETE_ACCOUNT_PATH)
async def delete_account(
    request: Request, principal: WebMutationPrincipalDependency, session: DatabaseSession
) -> Response:
    form = await read_form(request, fields={"email"}, max_bytes=MAX_DELETION_BODY_BYTES)
    typed = (form or {}).get("email", "")
    if not typed.strip():
        return _page(error=_("Type your email address to confirm."), status_code=422)
    repository = PostgreSQLAccountDeletionRepository(session)
    try:
        ConfirmAndDeleteAccount(
            emails=repository, delete_account=DeleteAccount(repository=repository)
        ).execute(user_id=principal.user_id, typed_email=typed)
    except DeletionNotConfirmed:
        return _page(
            email=typed,
            error=_("That is not the email address of this account. Nothing was deleted."),
            status_code=422,
        )
    response = htmx_redirect(ACCOUNT_DELETED_PATH)
    clear_authentication_cookies(response)
    return response


@router.get(ACCOUNT_DELETED_PATH)
async def account_deleted() -> Response:
    return render("account_deleted.html")
