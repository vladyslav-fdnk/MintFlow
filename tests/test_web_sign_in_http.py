from types import SimpleNamespace
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.config import Settings
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    get_authenticate_web_session,
    get_authentication_use_cases,
)
from mintflow.main import create_app
from mintflow.web.rendering import CONTENT_SECURITY_POLICY
from mintflow.web.testing import Element, parse_html

ORIGIN = "https://app.mintflow.test"
SECRET = "S" * 43
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Origin": ORIGIN}


class RecordingMagicLinkRequest:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def execute(
        self, *, submitted_email: str, normalized_network_source: str, return_target: str
    ) -> object:
        self.calls.append({"email": submitted_email, "return_target": return_target})
        return object()


class StubSessionAuthentication:
    def __init__(self, signed_in: bool) -> None:
        self.signed_in = signed_in

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        if self.signed_in and secret == SECRET:
            return AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4())
        return None


def _application(
    settings: Settings, *, signed_in: bool = False
) -> tuple[FastAPI, list[dict[str, str]]]:
    application = create_app(settings)
    recorder = RecordingMagicLinkRequest()
    application.dependency_overrides[get_authentication_use_cases] = lambda: SimpleNamespace(
        request_magic_link=recorder
    )
    application.dependency_overrides[get_authenticate_web_session] = lambda: (
        StubSessionAuthentication(signed_in)
    )
    return application, recorder.calls


async def _send(
    application: FastAPI,
    method: str,
    path: str,
    *,
    content: bytes | str | None = None,
    headers: dict[str, str] | None = None,
    signed_in: bool = False,
) -> Response:
    cookies = {AUTHENTICATED_SESSION_COOKIE_NAME: SECRET} if signed_in else None
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url=ORIGIN, cookies=cookies) as client:
        return await client.request(method, path, content=content, headers=headers)


def _email_input(page: Element) -> Element:
    return page.find("input", id="email")


@pytest.mark.anyio
async def test_the_sign_in_page_is_a_labelled_email_form(settings: Settings) -> None:
    application, _calls = _application(settings)

    response = await _send(application, "GET", "/sign-in")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    page = parse_html(response.text)
    form = page.find("form")
    assert (form.attrs["method"], form.attrs["action"]) == ("post", "/sign-in")
    assert page.find("label", for_="email").text == "Email address"
    field = _email_input(page)
    assert (field.attrs["type"], field.attrs["autocomplete"]) == ("email", "email")
    assert "aria-invalid" not in field.attrs
    assert page.find_all("nav") == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "signed_in", "location"),
    [
        ("/", False, "/sign-in"),
        ("/", True, "/dashboard"),
        ("/sign-in", True, "/dashboard"),
    ],
)
async def test_redirects_depend_on_being_signed_in(
    settings: Settings, path: str, signed_in: bool, location: str
) -> None:
    application, _calls = _application(settings, signed_in=True)

    response = await _send(application, "GET", path, signed_in=signed_in)

    assert (response.status_code, response.headers["location"]) == (303, location)


@pytest.mark.anyio
@pytest.mark.parametrize("email", ["ada@example.com", "  ada@example.com ", "адa@пример.рф"])
async def test_a_well_formed_address_always_leads_to_check_your_email(
    settings: Settings, email: str
) -> None:
    application, calls = _application(settings)

    response = await _send(
        application, "POST", "/sign-in", content=f"email={quote_plus(email)}", headers=FORM
    )

    assert (response.status_code, response.headers["location"]) == (303, "/sign-in/sent")
    assert calls == [{"email": email, "return_target": "dashboard"}]
    sent = await _send(application, "GET", "/sign-in/sent")
    assert parse_html(sent.text).find("h1").text == "Check your email"


@pytest.mark.anyio
async def test_a_malformed_address_gets_an_accessible_field_error(settings: Settings) -> None:
    application, calls = _application(settings)
    typed = '"><script>alert(1)</script>'

    response = await _send(
        application, "POST", "/sign-in", content=f"email={quote_plus(typed)}", headers=FORM
    )

    assert response.status_code == 400
    page = parse_html(response.text)
    field = _email_input(page)
    assert field.attrs["aria-invalid"] == "true"
    assert field.attrs["value"] == typed  # kept as typed, and escaped in the markup
    error = page.find(id=str(field.attrs["aria-describedby"]))
    assert error.text == "Enter an email address like name@example.com."
    assert "<script>alert" not in response.text
    # The use case records the attempt; it sends nothing for an invalid address.
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "headers"),
    [
        pytest.param("email=a%40b.co", {**FORM, "Origin": "https://evil.example"}, id="foreign"),
        pytest.param("email=a%40b.co", {"Content-Type": FORM["Content-Type"]}, id="no origin"),
    ],
)
async def test_a_foreign_origin_is_refused_and_nothing_is_sent(
    settings: Settings, content: str, headers: dict[str, str]
) -> None:
    application, calls = _application(settings)

    response = await _send(application, "POST", "/sign-in", content=content, headers=headers)

    assert response.status_code == 403
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "headers"),
    [
        pytest.param(
            '{"email": "a@b.co"}', {**FORM, "Content-Type": "application/json"}, id="json"
        ),
        pytest.param("email=a%40b.co&return_target=settings", FORM, id="extra field"),
        pytest.param("email=a%40b.co&email=c%40d.co", FORM, id="two emails"),
        pytest.param("", FORM, id="empty"),
        pytest.param("email=" + "a" * 1100, FORM, id="too large"),
    ],
)
async def test_malformed_submissions_ask_for_the_email_and_send_nothing(
    settings: Settings, content: str, headers: dict[str, str]
) -> None:
    application, calls = _application(settings)

    response = await _send(application, "POST", "/sign-in", content=content, headers=headers)

    assert response.status_code == 400
    assert parse_html(response.text).find(id="email-error").text == "Enter your email address."
    assert calls == []
