import pytest

from mintflow.http.authentication import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from mintflow.web.rendering import TEMPLATES, static_url
from mintflow.web.testing import Element, parse_html

# A page like every signed-in page: the base layout with a heading.
_SIGNED_IN_PAGE = TEMPLATES.from_string(
    '{% extends "base.html" %}{% block title %}Dashboard{% endblock %}'
    "{% block content %}<h1>Dashboard</h1>{% endblock %}"
)


def _page(template: str | None = None, **context: object) -> Element:
    page = _SIGNED_IN_PAGE if template is None else TEMPLATES.get_template(template)
    return parse_html(page.render(active="dashboard", **context))


def test_the_layout_has_landmarks_a_skip_link_and_a_status_region() -> None:
    page = _page()

    assert page.find("html").attrs["lang"] == "en"
    assert page.find("a", class_="skip-link").attrs["href"] == "#main"
    assert page.find("main").attrs["id"] == "main"
    assert page.find("nav").attrs["aria-label"] == "Main"
    status = page.find(id="status")
    assert (status.attrs["role"], status.attrs["aria-live"]) == ("status", "polite")
    assert page.find("h1").text == "Dashboard"


def test_navigation_marks_the_current_page_and_offers_sign_out() -> None:
    nav = _page().find("nav")

    current = nav.find("a", aria_current="page")
    assert (current.text, current.attrs["href"]) == ("Dashboard", "/dashboard")
    assert [link.text for link in nav.find_all("a")] == ["Dashboard", "Expenses", "Settings"]
    sign_out = nav.find("button")
    assert sign_out.text == "Sign out"
    assert sign_out.attrs["hx-post"] == "/auth/logout"
    assert sign_out.attrs["data-redirect-after"] == "/sign-in"


@pytest.mark.parametrize(
    ("template", "context"),
    [
        (None, {}),
        ("sign_in.html", {"email": "a@b", "error": "Wrong"}),
        ("sign_in_sent.html", {}),
        ("error.html", {"status_code": 404}),
        ("error.html", {"status_code": 500}),
    ],
)
def test_pages_contain_no_inline_script_or_style(
    template: str | None, context: dict[str, object]
) -> None:
    page = _page(template, **context)

    assert all("src" in script.attrs for script in page.find_all("script"))
    assert page.find_all("style") == []
    assert all("style" not in element.attrs for element in page.iter())
    assert not any(name.startswith("hx-on") for e in page.iter() for name in e.attrs)


def test_the_page_tells_the_script_which_csrf_cookie_and_header_to_use() -> None:
    body = _page().find("body")

    assert body.attrs["data-csrf-cookie"] == CSRF_COOKIE_NAME
    assert body.attrs["data-csrf-header"] == CSRF_HEADER_NAME


def test_assets_are_versioned_and_htmx_is_locked_down() -> None:
    page = _page()

    sources = [script.attrs["src"] for script in page.find_all("script")]
    assert sources == [static_url("vendor/htmx-2.0.11.min.js"), static_url("js/app.js")]
    assert page.find("link", rel="stylesheet").attrs["href"] == static_url("css/app.css")
    assert all("?v=" in str(source) for source in sources)
    config = page.find("meta", name="htmx-config").attrs["content"] or ""
    assert '"allowEval": false' in config and '"includeIndicatorStyles": false' in config


def test_error_pages_have_no_navigation() -> None:
    page = _page("error.html", status_code=404)

    assert page.find_all("nav") == []
    assert page.find("h1").text == "Page not found"


@pytest.mark.parametrize("name", ["missing.css", "../pages.py", "/etc/passwd"])
def test_static_urls_exist_only_for_static_files(name: str) -> None:
    with pytest.raises(ValueError):
        static_url(name)


def test_the_reader_keeps_text_in_document_order() -> None:
    paragraph = parse_html("<p>Spent <b>12.50</b> today<br>at <i>Kiosk</i></p>").find("p")

    assert paragraph.text == "Spent 12.50 today at Kiosk"
