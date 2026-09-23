"""Web pages and their authentication (web design W1, W2).

A page request without a valid session is redirected to sign-in with ``303``; the JSON API keeps
answering ``401``. An error renders a generic HTML page only for a browser (``Accept`` includes
``text/html``) outside the API paths; every other client keeps FastAPI's JSON errors.
"""

from typing import Annotated, Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse, Response

from mintflow.http.authentication import (
    AuthenticatedPrincipal,
    AuthenticateWebSessionDependency,
    get_authenticated_principal,
)
from mintflow.web.rendering import STATIC_PATH, redirect, render, static_files

SIGN_IN_PATH: Final = "/sign-in"
# Paths served by the JSON API or by authentication; everything else is a Web page.
API_PATH_PREFIXES: Final = ("/auth/", "/capture/", "/analytics/", "/telegram/", "/health/")

router = APIRouter(include_in_schema=False)


class SignInRequired(Exception):
    """The page needs a signed-in user; answered with a redirect to sign-in."""


async def get_page_principal(
    request: Request, authenticate: AuthenticateWebSessionDependency
) -> AuthenticatedPrincipal:
    try:
        return await get_authenticated_principal(request, authenticate)
    except HTTPException as error:
        if error.status_code == 401:
            raise SignInRequired from None
        raise


PagePrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(get_page_principal)]


@router.get("/dashboard")
async def dashboard(principal: PagePrincipalDependency) -> Response:
    # A placeholder until WEB-03 renders the dashboard itself.
    return render("dashboard.html", {"active": "dashboard"})


def wants_html_error(request: Request) -> bool:
    path = request.url.path
    return (
        "text/html" in request.headers.get("accept", "")
        and not path.startswith(API_PATH_PREFIXES)
        and path not in {"/docs", "/openapi.json"}
    )


async def _sign_in_required(request: Request, exc: Exception) -> Response:
    return redirect(SIGN_IN_PATH)


async def _http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if not wants_html_error(request):
        return await http_exception_handler(request, exc)
    return render("error.html", {"status_code": exc.status_code}, status_code=exc.status_code)


async def _server_error(request: Request, exc: Exception) -> Response:
    if not wants_html_error(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return render("error.html", {"status_code": 500}, status_code=500)


def install_web(application: FastAPI) -> None:
    application.include_router(router)
    application.mount(STATIC_PATH, static_files(), name="static")
    application.add_exception_handler(SignInRequired, _sign_in_required)
    application.add_exception_handler(StarletteHTTPException, _http_error)
    application.add_exception_handler(Exception, _server_error)
