"""Templates, static files, and the headers every Web response carries (web design W1, W5).

Static URLs carry a content hash (``?v=``), so browsers may cache them for a year and still pick
up a new version at once. HTML is never cached: pages show one user's financial data.
"""

import hashlib
import os
from collections.abc import Mapping
from functools import cache
from os import PathLike
from pathlib import Path
from typing import Final

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from mintflow.http.authentication import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from mintflow.web.formatting import _, format_date, format_money

STATIC_DIRECTORY: Final = Path(__file__).parent / "static"
STATIC_PATH: Final = "/static"

CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
_COMMON_HEADERS: Final = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
PAGE_HEADERS: Final = {
    **_COMMON_HEADERS,
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Cache-Control": "no-store",
}
_VERSIONED_STATIC_CACHE: Final = "public, max-age=31536000, immutable"


@cache
def _static_version(name: str) -> str:
    path = (STATIC_DIRECTORY / name).resolve()
    if not path.is_relative_to(STATIC_DIRECTORY.resolve()) or not path.is_file():
        raise ValueError(f"unknown static file {name!r}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def static_url(name: str) -> str:
    return f"{STATIC_PATH}/{name}?v={_static_version(name)}"


def _environment() -> Environment:
    environment = Environment(
        loader=PackageLoader("mintflow.web", "templates"),
        autoescape=select_autoescape(default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.globals.update(
        _=_,
        static_url=static_url,
        csrf_cookie_name=CSRF_COOKIE_NAME,
        csrf_header_name=CSRF_HEADER_NAME,
    )
    environment.filters.update(money=format_money, date=format_date)
    return environment


TEMPLATES: Final = _environment()


def render(
    template: str, context: Mapping[str, object] | None = None, *, status_code: int = 200
) -> HTMLResponse:
    html = TEMPLATES.get_template(template).render(**(context or {}))
    return HTMLResponse(html, status_code=status_code, headers=PAGE_HEADERS)


def redirect(url: str) -> RedirectResponse:
    """A 303, so a redirect after a form post is always followed with GET."""
    return RedirectResponse(url, status_code=303, headers=PAGE_HEADERS)


def htmx_redirect(url: str) -> Response:
    """After an htmx mutation: the browser navigates to ``url`` (the ``HX-Redirect`` header)."""
    return Response(status_code=200, headers={**PAGE_HEADERS, "HX-Redirect": url})


class WebStaticFiles(StaticFiles):
    """Static files with the common headers; versioned URLs are cached for a year."""

    def file_response(
        self,
        full_path: PathLike[str] | str,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers.update(_COMMON_HEADERS)
        versioned = scope.get("query_string", b"").startswith(b"v=")
        response.headers["Cache-Control"] = _VERSIONED_STATIC_CACHE if versioned else "no-cache"
        return response


def static_files() -> WebStaticFiles:
    return WebStaticFiles(directory=STATIC_DIRECTORY)
