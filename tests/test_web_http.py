from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import Response as FastAPIResponse
from httpx import ASGITransport, AsyncClient, Response

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.config import Settings
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
    CsrfProtectedPrincipalDependency,
    get_authenticate_web_session,
)
from mintflow.main import create_app
from mintflow.web import PagePrincipalDependency
from mintflow.web.pages import get_user_lookup
from mintflow.web.rendering import CONTENT_SECURITY_POLICY, render, static_url
from mintflow.web.testing import parse_html

ORIGIN = "https://app.mintflow.test"
SECRET = "S" * 43
HTML = {"Accept": "text/html,application/xhtml+xml"}


class NoUsers:
    """No account to read a language from: pages keep the browser's language."""

    def get(self, user_id: object) -> None:
        return None


class StubSessionAuthentication:
    def __init__(self, result: AuthenticatedWebSession | None) -> None:
        self.result = result

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        return self.result if secret == SECRET else None


def _application(settings: Settings, *, signed_in: bool) -> FastAPI:
    application = create_app(settings)
    session = AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4()) if signed_in else None
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(session)
    )
    application.dependency_overrides[get_user_lookup] = lambda: NoUsers()

    @application.post("/test/web-mutation")
    async def mutation(principal: CsrfProtectedPrincipalDependency) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    @application.get("/test/web-page")
    async def page(principal: PagePrincipalDependency) -> FastAPIResponse:
        return render("sign_in_sent.html")

    @application.get("/test/web-layout")
    async def layout(principal: PagePrincipalDependency) -> FastAPIResponse:
        return render("expense_deleted.html", {"active": "expenses", "expense_id": "x"})

    @application.get("/test/web-failure")
    async def failure() -> None:
        raise RuntimeError("boom")

    return application


async def _request(
    application: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Response:
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN, cookies=cookies) as client:
        return await client.request(method, path, headers=headers)


def _assert_page_headers(response: Response) -> None:
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


@pytest.mark.anyio
@pytest.mark.parametrize("cookie", [None, "malformed", "A" * 43])
async def test_pages_redirect_to_sign_in_without_a_valid_session(
    settings: Settings, cookie: str | None
) -> None:
    cookies = None if cookie is None else {AUTHENTICATED_SESSION_COOKIE_NAME: cookie}

    response = await _request(
        _application(settings, signed_in=True), "GET", "/dashboard", headers=HTML, cookies=cookies
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in"
    _assert_page_headers(response)


@pytest.mark.anyio
async def test_the_json_api_still_answers_401(settings: Settings) -> None:
    response = await _request(
        _application(settings, signed_in=False), "GET", "/capture/expenses", headers=HTML
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


@pytest.mark.anyio
async def test_a_signed_in_page_is_rendered_with_the_security_headers(settings: Settings) -> None:
    response = await _request(
        _application(settings, signed_in=True),
        "GET",
        "/test/web-page",
        cookies={AUTHENTICATED_SESSION_COOKIE_NAME: SECRET},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    _assert_page_headers(response)


@pytest.mark.anyio
async def test_versioned_static_files_are_cached_for_a_year(settings: Settings) -> None:
    application = _application(settings, signed_in=False)

    versioned = await _request(application, "GET", static_url("js/app.js"))
    plain = await _request(application, "GET", "/static/js/app.js")

    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert versioned.headers["x-content-type-options"] == "nosniff"
    assert "htmx:configRequest" in versioned.text
    assert plain.headers["cache-control"] == "no-cache"


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/static/../pages.py", "/static/%2e%2e/pages.py"])
async def test_static_files_never_serve_source(settings: Settings, path: str) -> None:
    response = await _request(_application(settings, signed_in=False), "GET", path)

    assert response.status_code == 404
    assert "def " not in response.text


@pytest.mark.anyio
async def test_browsers_get_an_html_404_and_other_clients_json(settings: Settings) -> None:
    application = _application(settings, signed_in=False)

    page = await _request(application, "GET", "/no-such-page", headers=HTML)
    json_client = await _request(application, "GET", "/no-such-page")
    api = await _request(application, "GET", "/capture/no-such-route", headers=HTML)

    assert page.status_code == 404
    _assert_page_headers(page)
    assert parse_html(page.text).find("h1").text == "Page not found"
    assert json_client.status_code == api.status_code == 404
    assert json_client.json() == api.json() == {"detail": "Not Found"}


@pytest.mark.anyio
async def test_an_unexpected_error_shows_a_generic_page_without_details(
    settings: Settings,
) -> None:
    application = _application(settings, signed_in=False)

    page = await _request(application, "GET", "/test/web-failure", headers=HTML)
    plain = await _request(application, "GET", "/test/web-failure")

    assert page.status_code == 500
    _assert_page_headers(page)
    assert parse_html(page.text).find("h1").text == "Something went wrong"
    assert "boom" not in page.text and "RuntimeError" not in page.text
    assert (plain.status_code, plain.text) == (500, "Internal Server Error")


def _csrf_token(application: FastAPI) -> str:
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    return runtime.csrf_digester.derive(session_secret=SECRET)


@pytest.mark.anyio
async def test_mutations_need_the_csrf_header_the_script_sends(settings: Settings) -> None:
    application = _application(settings, signed_in=True)
    token = _csrf_token(application)
    cookies = {AUTHENTICATED_SESSION_COOKIE_NAME: SECRET, CSRF_COOKIE_NAME: token}

    without = await _request(
        application, "POST", "/test/web-mutation", headers={"Origin": ORIGIN}, cookies=cookies
    )
    # What app.js adds to every htmx request: the cookie's value in the header.
    with_header = await _request(
        application,
        "POST",
        "/test/web-mutation",
        headers={"Origin": ORIGIN, CSRF_HEADER_NAME: token, "HX-Request": "true"},
        cookies=cookies,
    )

    assert without.status_code == 403
    assert with_header.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "content_type"),
    [("favicon.svg", "image/svg+xml"), ("fonts/onest-latin.woff2", "font/woff2")],
)
async def test_brand_assets_are_served_as_static_files(
    settings: Settings, name: str, content_type: str
) -> None:
    response = await _request(_application(settings, signed_in=False), "GET", static_url(name))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cookie", "theme", "checked"),
    [
        ("dark", "dark", "true"),
        ("light", "light", "false"),
        ("purple", None, "false"),
        (None, None, "false"),
    ],
)
async def test_pages_render_in_the_theme_chosen_in_this_browser(
    settings: Settings, cookie: str | None, theme: str | None, checked: str
) -> None:
    cookies = {AUTHENTICATED_SESSION_COOKIE_NAME: SECRET}
    if cookie is not None:
        cookies["mintflow_theme"] = cookie

    response = await _request(
        _application(settings, signed_in=True), "GET", "/test/web-page", cookies=cookies
    )

    page = parse_html(response.text)
    assert page.find("html").attrs.get("data-theme") == theme
    layout = parse_html(
        (
            await _request(
                _application(settings, signed_in=True), "GET", "/test/web-layout", cookies=cookies
            )
        ).text
    )
    sidebar_switch = layout.find("nav").find("button", role="switch")
    assert (sidebar_switch.attrs["aria-checked"], sidebar_switch.text) == (checked, "Dark theme")
    phone_switch = layout.find("header", class_="topbar").find("button", role="switch")
    assert phone_switch.attrs["aria-checked"] == checked
