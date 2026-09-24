"""Web pages and their authentication (web design W1, W2).

A page request without a valid session is redirected to sign-in with ``303``; the JSON API keeps
answering ``401``. An error renders a generic HTML page only for a browser (``Accept`` includes
``text/html``) outside the API paths; every other client keeps FastAPI's JSON errors.
"""

from typing import Annotated, Final, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse, Response

from mintflow.domain.user import User
from mintflow.http.authentication import (
    AuthenticatedPrincipal,
    AuthenticateWebSessionDependency,
    CsrfProtectedPrincipalDependency,
    DatabaseSession,
    get_authenticated_principal,
)
from mintflow.infrastructure.persistence import SqlAlchemyUserRepository
from mintflow.web.formatting import use_language
from mintflow.web.rendering import redirect, render

SIGN_IN_PATH: Final = "/sign-in"
# Paths served by the JSON API or by authentication; everything else is a Web page.
API_PATH_PREFIXES: Final = ("/auth/", "/capture/", "/analytics/", "/telegram/", "/health/")

router = APIRouter(include_in_schema=False)


class SignInRequired(Exception):
    """The page needs a signed-in user; answered with a redirect to sign-in."""


class UserLookup(Protocol):
    def get(self, user_id: UUID) -> User | None: ...


async def get_user_lookup(session: DatabaseSession) -> UserLookup:
    return SqlAlchemyUserRepository(session)


UserLookupDependency = Annotated[UserLookup, Depends(get_user_lookup)]


def _use_account_language(users: UserLookup, principal: AuthenticatedPrincipal) -> None:
    """Signed-in pages speak the account's language, not the browser's (design W14)."""
    user = users.get(principal.user_id)
    if user is not None:
        use_language(user.ui_language.value)


async def get_page_principal(
    request: Request, authenticate: AuthenticateWebSessionDependency, users: UserLookupDependency
) -> AuthenticatedPrincipal:
    try:
        principal = await get_authenticated_principal(request, authenticate)
    except HTTPException as error:
        if error.status_code == 401:
            raise SignInRequired from None
        raise
    _use_account_language(users, principal)
    return principal


async def get_web_mutation_principal(
    principal: CsrfProtectedPrincipalDependency, users: UserLookupDependency
) -> AuthenticatedPrincipal:
    """The CSRF-protected principal of a Web mutation, with the account's language."""
    _use_account_language(users, principal)
    return principal


PagePrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(get_page_principal)]
WebMutationPrincipalDependency = Annotated[
    AuthenticatedPrincipal, Depends(get_web_mutation_principal)
]


def non_empty_params(request: Request) -> QueryParams:
    """The query without empty values: an emptied form field means "not set", not invalid."""
    return QueryParams([(key, value) for key, value in request.query_params.multi_items() if value])


def wants_html_error(request: Request) -> bool:
    path = request.url.path
    return (
        "text/html" in request.headers.get("accept", "")
        and not path.startswith(API_PATH_PREFIXES)
        and path not in {"/docs", "/openapi.json"}
    )


async def sign_in_required(request: Request, exc: Exception) -> Response:
    return redirect(SIGN_IN_PATH)


async def http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if not wants_html_error(request):
        return await http_exception_handler(request, exc)
    return render("error.html", {"status_code": exc.status_code}, status_code=exc.status_code)


async def server_error(request: Request, exc: Exception) -> Response:
    if not wants_html_error(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return render("error.html", {"status_code": 500}, status_code=500)
