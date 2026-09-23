"""Sign-in with a magic link, and the root redirect (web design W2).

The form works without JavaScript. It accepts only a first-party Origin, a small URL-encoded
body, and one email field. Every accepted submission goes through ``RequestMagicLink``, so
rate limits and audit records match the JSON endpoint, and the answer never reveals whether an
account exists: a well-formed address always leads to the same "check your email" page. Only an
address that is not an email at all gets a field error, which reveals nothing about accounts.
"""

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import Response

from mintflow.application.authentication.email import InvalidEmailError, normalize_email
from mintflow.http.authentication import (
    MAX_SUBMITTED_EMAIL_LENGTH,
    AuthenticatedPrincipal,
    AuthenticateWebSessionDependency,
    AuthenticationRuntime,
    AuthenticationUseCasesDependency,
    NetworkSourceDependency,
    get_authenticated_principal,
    has_approved_login_origin,
)
from mintflow.web.formatting import _
from mintflow.web.forms import read_form
from mintflow.web.pages import SIGN_IN_PATH
from mintflow.web.rendering import redirect, render

SIGN_IN_SENT_PATH: Final = "/sign-in/sent"
DASHBOARD_PATH: Final = "/dashboard"
# The return target every Web sign-in link leads back to (it must be an approved target).
SIGN_IN_RETURN_TARGET: Final = "dashboard"
MAX_SIGN_IN_BODY_BYTES: Final = 1_024

router = APIRouter(include_in_schema=False)


async def get_optional_principal(
    request: Request, authenticate: AuthenticateWebSessionDependency
) -> AuthenticatedPrincipal | None:
    try:
        return await get_authenticated_principal(request, authenticate)
    except HTTPException as error:
        if error.status_code == 401:
            return None
        raise


OptionalPrincipalDependency = Annotated[
    AuthenticatedPrincipal | None, Depends(get_optional_principal)
]


async def _submitted_email(request: Request) -> str | None:
    form = await read_form(request, fields={"email"}, max_bytes=MAX_SIGN_IN_BODY_BYTES)
    return form.get("email") if form is not None else None


def _form(*, email: str = "", error: str | None = None, status_code: int = 200) -> Response:
    return render("sign_in.html", {"email": email, "error": error}, status_code=status_code)


@router.get("/")
async def root(principal: OptionalPrincipalDependency) -> Response:
    return redirect(DASHBOARD_PATH if principal is not None else SIGN_IN_PATH)


@router.get(SIGN_IN_PATH)
async def sign_in_page(principal: OptionalPrincipalDependency) -> Response:
    if principal is not None:
        return redirect(DASHBOARD_PATH)
    return _form()


@router.post(SIGN_IN_PATH)
async def request_sign_in_link(
    request: Request,
    use_cases: AuthenticationUseCasesDependency,
    network_source: NetworkSourceDependency,
) -> Response:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    if not has_approved_login_origin(
        request=request, approved_origin=runtime.link_builder.web_origin
    ):
        return render("error.html", {"status_code": 403}, status_code=403)
    email = await _submitted_email(request)
    if email is None or len(email) > MAX_SUBMITTED_EMAIL_LENGTH:
        return _form(error=_("Enter your email address."), status_code=400)
    # The use case records the attempt and sends nothing for an invalid address.
    use_cases.request_magic_link.execute(
        submitted_email=email,
        normalized_network_source=network_source.value,
        return_target=SIGN_IN_RETURN_TARGET,
    )
    try:
        normalize_email(email)
    except InvalidEmailError:
        return _form(
            email=email,
            error=_("Enter an email address like name@example.com."),
            status_code=400,
        )
    return redirect(SIGN_IN_SENT_PATH)


@router.get(SIGN_IN_SENT_PATH)
async def sign_in_link_sent() -> Response:
    return render("sign_in_sent.html")
