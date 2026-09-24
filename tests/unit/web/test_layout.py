import os
from pathlib import Path

import pytest

from mintflow.http.authentication import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from mintflow.web import rendering
from mintflow.web.formatting import use_language
from mintflow.web.rendering import TEMPLATES, static_url, theme_from_cookie_header
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
    sign_out = nav.find("button", hx_post="/auth/logout")
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
    stylesheets = [link.attrs["href"] for link in page.find_all("link", rel="stylesheet")]
    assert stylesheets == [static_url("css/app.css"), static_url("css/noscript.css")]
    # The no-script stylesheet only applies without JavaScript.
    assert page.find("noscript").find("link", rel="stylesheet").attrs["href"] == static_url(
        "css/noscript.css"
    )
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


def test_there_is_one_navigation_whose_labels_exist_while_collapsed() -> None:
    page = _page()

    [nav] = page.find_all("nav")
    # Labels only fade visually; they stay in the markup, so icons keep their names.
    assert [label.text for label in nav.find_all("span", class_="label")] == [
        "Dashboard",
        "Expenses",
        "Settings",
        "Русский",  # the quick switch names the other language in that language
        "Dark theme",
        "Sign out",
    ]
    assert all(svg.attrs["aria-hidden"] == "true" for svg in nav.find_all("svg"))
    brand = page.find("aside", class_="sidebar is-resting").find("a", class_="brand")
    assert brand.attrs["aria-label"] == "MintFlow, dashboard"


def test_the_favicon_and_font_are_versioned_static_files() -> None:
    page = _page()

    icon = page.find("link", rel="icon")
    assert (icon.attrs["type"], icon.attrs["href"]) == (
        "image/svg+xml",
        static_url("favicon.svg"),
    )
    font = page.find("link", rel="preload")
    assert font.attrs["href"] == static_url("fonts/onest-latin.woff2")
    assert "crossorigin" in font.attrs


def test_pages_without_navigation_still_show_the_brand() -> None:
    page = _page("error.html", status_code=404)

    assert page.find_all("nav") == []
    assert page.find("header", class_="bare-header").find("a", class_="brand").text == "MintFlow"


@pytest.mark.parametrize(
    ("template", "context"),
    [
        ("sign_in_sent.html", {}),
        ("magic_link_confirm.html", {"token": "T", "return_target": "dashboard", "action": "/a"}),
        ("magic_link_unavailable.html", {}),
    ],
)
def test_the_sign_in_steps_share_the_branded_card(
    template: str, context: dict[str, object]
) -> None:
    page = _page(template, **context)

    assert page.find_all("nav") == []
    assert page.find("p", class_="lockup-wordmark").text == "MintFlow"
    assert page.find("div", class_="form card").find("h1", class_="card-title").text


def _empty_view() -> object:
    from mintflow.web.dashboard import DashboardView

    return DashboardView(
        period="Sep 2026",
        date_from="2026-09-01",
        date_to="2026-09-30",
        currency=None,
        currency_options=(),
        currencies=(),
        selection_required=False,
        detail=None,
        chart="columns",
        chart_links=(),
        presets=(),
    )


def test_every_mark_on_a_page_has_its_own_gradient() -> None:
    page = parse_html(
        TEMPLATES.get_template("dashboard.html").render(
            active="dashboard", view=_empty_view(), invalid_filters=False, onboarding=None
        )
    )

    ids = [gradient.attrs["id"] for gradient in page.find_all("lineargradient")]
    assert len(ids) == len(set(ids)) >= 2


def test_the_reader_joins_inline_elements_and_separates_blocks() -> None:
    assert parse_html("<p><span>Mint</span><span>Flow</span></p>").find("p").text == "MintFlow"
    assert (
        parse_html("<table><tr><th>Sep 1</th><td>4.00</td></tr></table>").find("tr").text
        == "Sep 1 4.00"
    )


@pytest.mark.parametrize(
    ("header", "theme"),
    [
        ("mintflow_theme=dark", "dark"),
        ("a=1; mintflow_theme=light; b=2", "light"),
        ("mintflow_theme=sepia", None),
        ("", None),
        ('broken="', None),
    ],
)
def test_only_known_themes_are_read_from_the_cookie(header: str, theme: str | None) -> None:
    assert theme_from_cookie_header(header) == theme


def test_the_language_switch_offers_the_other_language() -> None:
    page = _page(language="ru")
    switch = page.find("nav").find("button", hx_post="/settings/language")
    phone = page.find("header", class_="topbar").find("button", hx_post="/settings/language")
    assert (phone.attrs["aria-label"], phone.text) == ("English", "EN")

    assert switch.attrs["hx-vals"] == '{"language": "en"}'
    assert (switch.attrs["lang"], switch.text) == ("en", "English")


def test_phones_get_the_extra_controls_in_the_top_bar_with_names() -> None:
    actions = _page().find("header", class_="topbar").find("div", class_="topbar-actions")

    names = [button.attrs["aria-label"] for button in actions.find_all("button")]
    assert names == ["Русский", "Dark theme", "Sign out"]
    theme = actions.find("button", role="switch")
    assert "data-theme-switch" in theme.attrs


def test_an_edited_static_file_gets_a_new_url_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rendering, "STATIC_DIRECTORY", tmp_path)
    stylesheet = tmp_path / "app.css"
    stylesheet.write_text("body { color: red; }")
    before = static_url("app.css")

    stylesheet.write_text("body { color: blue; }")
    os.utime(stylesheet, ns=(1, stylesheet.stat().st_mtime_ns + 1_000_000))

    assert static_url("app.css") != before


def test_the_failure_message_for_htmx_requests_is_translated() -> None:
    use_language("ru")
    try:
        page = parse_html(_SIGNED_IN_PAGE.render(active="dashboard"))
    finally:
        use_language("en")

    assert page.find(id="status").attrs["data-error-message"] == (
        "Что-то пошло не так. Попробуйте ещё раз."
    )
