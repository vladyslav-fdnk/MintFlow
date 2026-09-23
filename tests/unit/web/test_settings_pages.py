from collections.abc import Callable

import pytest

from mintflow.application.telegram import TelegramLinkState
from mintflow.web.rendering import TEMPLATES
from mintflow.web.settings import PreferencesForm, TelegramView
from mintflow.web.testing import Element, parse_html

FORM = PreferencesForm(timezone="UTC", default_currency="EUR", locale="")
DISCONNECTED = TelegramView(True, False, None, None, "https://t.me/bot")


def _settings(**overrides: object) -> Element:
    context: dict[str, object] = {
        "active": "settings",
        "form": FORM,
        "errors": {},
        "form_error": None,
        "timezones": ("Asia/Tokyo", "UTC"),
        "currencies": ("EUR", "PLN"),
        "locales": [("de-DE", "Deutsch (Deutschland)")],
        "telegram": DISCONNECTED,
        "status_message": "",
    }
    context.update(overrides)
    return parse_html(TEMPLATES.get_template("settings.html").render(**context))


def _link_status(state: TelegramLinkState) -> Element:
    html = TEMPLATES.get_template("settings_link.html").render(
        challenge_id="c1",
        deep_link="https://t.me/bot?start=token",
        state=state.value,
        states=TelegramLinkState,
        display_name="Ada",
        error=None,
    )
    return parse_html(html)


def test_every_preference_is_labelled_and_explained() -> None:
    form = _settings().find("form", id="preferences-form")

    for field in ("timezone", "default_currency", "locale"):
        assert form.find("label", for_=field).text
    assert form.find("select", id="timezone").attrs["aria-describedby"] == "timezone-hint"
    assert "data-warn-unsaved" in form.attrs


def test_an_error_is_linked_to_its_field_without_losing_the_hint() -> None:
    field = _settings(errors={"timezone": "Choose a timezone from the list."}).find(
        "select", id="timezone"
    )

    assert field.attrs["aria-invalid"] == "true"
    assert field.attrs["aria-describedby"] == "timezone-hint timezone-error"


@pytest.mark.parametrize(
    ("state", "polls"),
    [
        (TelegramLinkState.WAITING_FOR_TELEGRAM, True),
        (TelegramLinkState.AWAITING_CONFIRMATION, False),
        (TelegramLinkState.CONNECTED, False),
        (TelegramLinkState.EXPIRED, False),
    ],
)
def test_only_a_waiting_link_keeps_polling(state: TelegramLinkState, polls: bool) -> None:
    section = _link_status(state).find("section", id="telegram")

    status = section.find("div", id="link-status")
    assert ("hx-trigger" in status.attrs) is polls
    # Announcements come from a region that stays in place while the status is swapped.
    assert status.parent is not None and status.parent.attrs["aria-live"] == "polite"


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(lambda: _settings(), id="settings"),
        pytest.param(lambda: _link_status(TelegramLinkState.AWAITING_CONFIRMATION), id="link"),
        pytest.param(
            lambda: parse_html(
                TEMPLATES.get_template("settings_disconnect.html").render(
                    active="settings", telegram=TelegramView(True, True, "Ada", "now", "u")
                )
            ),
            id="disconnect",
        ),
    ],
)
def test_settings_pages_have_no_inline_script_or_style(page: Callable[[], Element]) -> None:
    rendered = page()

    assert all("src" in script.attrs for script in rendered.find_all("script"))
    assert all("style" not in element.attrs for element in rendered.iter())
