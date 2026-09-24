"""Settings: conventions and the Telegram connection (web design W8, W9; MVP section 6).

Preferences are validated field by field so each error lands on its field, then saved together
by ``UpdatePreferences``. Linking Telegram follows the existing ceremony: this Web session
creates a one-time link, the user opens it in Telegram, and this session confirms. The link's
token exists only in the response that creates it, so that response shows the "Open Telegram"
button and then polls the challenge's status.
"""

from dataclasses import dataclass
from typing import Annotated, Final
from uuid import UUID

from babel import Locale as BabelLocale
from babel import UnknownLocaleError
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import Response

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.telegram import (
    GetTelegramConnection,
    GetTelegramLinkStatus,
    IssueTelegramLinkChallenge,
    TelegramLinkState,
    UnlinkTelegram,
)
from mintflow.application.users import ChangeLanguage, UpdatePreferences
from mintflow.domain.capture import CurrencyCode, supported_currency_codes
from mintflow.domain.user import Locale, Timezone, User, available_timezone_names
from mintflow.http.authentication import (
    AuthenticatedPrincipal,
    DatabaseSession,
)
from mintflow.http.telegram import confirm_telegram_link
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyUserRepository,
)
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.web.formatting import (
    N_,
    SUPPORTED_LANGUAGES,
    _,
    current_language,
    display_locale,
    format_datetime,
)
from mintflow.web.forms import read_form
from mintflow.web.pages import (
    PagePrincipalDependency,
    SignInRequired,
    WebMutationPrincipalDependency,
)
from mintflow.web.rendering import PAGE_HEADERS, htmx_redirect, render

SETTINGS_PATH: Final = "/settings"
MAX_PREFERENCES_BODY_BYTES: Final = 1_024
# Locales offered in the picker; a stored locale outside this list is still shown and kept.
OFFERED_LOCALES: Final = (
    "en",
    "en-GB",
    "en-US",
    "de-DE",
    "es-ES",
    "fr-FR",
    "it-IT",
    "ja-JP",
    "pl-PL",
    "pt-BR",
    "ru-RU",
    "uk-UA",
)
_STATUS_MESSAGES: Final = {
    "saved": N_("Settings saved."),
    "connected": N_("Telegram connected."),
    "disconnected": N_("Telegram disconnected."),
}
# Each language is named in itself, so a reader can find their own.
LANGUAGE_NAMES: Final = {"en": "English", "ru": "Русский"}

router = APIRouter(include_in_schema=False)


def get_optional_telegram(request: Request) -> TelegramRuntime | None:
    runtime: TelegramRuntime | None = getattr(request.app.state, "telegram_runtime", None)
    return runtime


def get_telegram(request: Request) -> TelegramRuntime:
    runtime = get_optional_telegram(request)
    if runtime is None:
        raise HTTPException(status_code=404)
    return runtime


OptionalTelegramDependency = Annotated[TelegramRuntime | None, Depends(get_optional_telegram)]
TelegramDependency = Annotated[TelegramRuntime, Depends(get_telegram)]


@dataclass(frozen=True, slots=True)
class PreferencesForm:
    timezone: str
    default_currency: str
    locale: str
    language: str = "en"


@dataclass(frozen=True, slots=True)
class TelegramView:
    available: bool
    connected: bool
    display_name: str | None
    linked_at: str | None
    bot_url: str | None


def _locale_name(tag: str) -> str:
    try:
        parsed = BabelLocale.parse(tag, sep="-")
    except (ValueError, UnknownLocaleError):
        return tag
    return (parsed.get_display_name(parsed) or tag).capitalize()


def _user(session: DatabaseSession, principal: AuthenticatedPrincipal) -> User:
    user = SqlAlchemyUserRepository(session).get(principal.user_id)
    if user is None:
        raise SignInRequired
    return user


def _form_from_user(user: User) -> PreferencesForm:
    return PreferencesForm(
        timezone=user.timezone.value,
        default_currency=user.default_currency.value if user.default_currency else "",
        locale=user.locale.value if user.locale else "",
        language=user.ui_language.value,
    )


def _telegram_view(
    session: DatabaseSession, user: User, telegram: TelegramRuntime | None
) -> TelegramView:
    if telegram is None:
        return TelegramView(False, False, None, None, None)
    connection = GetTelegramConnection(
        repository=SqlAlchemyTelegramLinkRepository(session)
    ).execute(user_id=user.id)
    return TelegramView(
        available=True,
        connected=connection is not None,
        display_name=connection.telegram_display_name if connection is not None else None,
        linked_at=(
            format_datetime(connection.linked_at, user.timezone, display_locale(user.locale))
            if connection is not None
            else None
        ),
        bot_url=f"https://t.me/{telegram.bot_username}",
    )


def _settings_page(
    session: DatabaseSession,
    user: User,
    telegram: TelegramRuntime | None,
    *,
    form: PreferencesForm | None = None,
    errors: dict[str, str] | None = None,
    form_error: str | None = None,
    status_message: str = "",
    status_code: int = 200,
) -> Response:
    shown = form or _form_from_user(user)
    locales = list(OFFERED_LOCALES)
    if shown.locale and shown.locale not in locales:
        locales.append(shown.locale)
    return render(
        "settings.html",
        {
            "active": "settings",
            "form": shown,
            "errors": errors or {},
            "form_error": form_error,
            "timezones": available_timezone_names(),
            "currencies": supported_currency_codes(),
            "locales": [(tag, _locale_name(tag)) for tag in locales],
            "languages": list(LANGUAGE_NAMES.items()),
            "telegram": _telegram_view(session, user, telegram),
            "status_message": status_message,
        },
        status_code=status_code,
    )


@router.get(SETTINGS_PATH)
async def settings_page(
    request: Request,
    principal: PagePrincipalDependency,
    session: DatabaseSession,
    telegram: OptionalTelegramDependency,
) -> Response:
    message = _STATUS_MESSAGES.get(request.query_params.get("done", ""))
    return _settings_page(
        session,
        _user(session, principal),
        telegram,
        status_message=_(message) if message else "",
    )


def _validate(form: PreferencesForm) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        Timezone(form.timezone)
    except ValueError:
        errors["timezone"] = _("Choose a timezone from the list.")
    if form.default_currency:
        try:
            CurrencyCode(form.default_currency)
        except ValueError:
            errors["default_currency"] = _("Choose a currency from the list.")
    if form.locale:
        try:
            Locale(form.locale)
        except ValueError:
            errors["locale"] = _("Choose a format from the list.")
    if form.language not in SUPPORTED_LANGUAGES:
        errors["language"] = _("Choose a language from the list.")
    return errors


@router.post(SETTINGS_PATH + "/preferences")
async def save_preferences(
    request: Request,
    principal: WebMutationPrincipalDependency,
    session: DatabaseSession,
    telegram: OptionalTelegramDependency,
) -> Response:
    user = _user(session, principal)
    values = await read_form(
        request,
        fields={"timezone", "default_currency", "locale", "language"},
        max_bytes=MAX_PREFERENCES_BODY_BYTES,
    )
    form = (
        PreferencesForm(
            timezone=values.get("timezone", ""),
            default_currency=values.get("default_currency", ""),
            locale=values.get("locale", ""),
            language=values.get("language", current_language()),
        )
        if values is not None
        else None
    )
    if form is None:
        return _settings_page(
            session,
            user,
            telegram,
            form_error=_("The form could not be read. Please try again."),
            status_code=422,
        )
    errors = _validate(form)
    if errors:
        return _settings_page(session, user, telegram, form=form, errors=errors, status_code=422)
    UpdatePreferences(repository=SqlAlchemyUserRepository(session)).execute(
        user_id=user.id,
        timezone=form.timezone,
        default_currency=form.default_currency or None,
        locale=form.locale or None,
        ui_language=form.language,
    )
    return htmx_redirect(f"{SETTINGS_PATH}?done=saved")


@router.post(SETTINGS_PATH + "/language")
async def change_language(
    request: Request, principal: WebMutationPrincipalDependency, session: DatabaseSession
) -> Response:
    """The sidebar's quick switch: save the language, then reload the page in it."""
    values = await read_form(request, fields={"language"}, max_bytes=MAX_PREFERENCES_BODY_BYTES)
    language = values.get("language") if values is not None else None
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=422)
    ChangeLanguage(repository=SqlAlchemyUserRepository(session)).execute(
        user_id=principal.user_id, ui_language=language
    )
    return Response(status_code=200, headers={**PAGE_HEADERS, "HX-Refresh": "true"})


# --- Telegram ---------------------------------------------------------------------------------


def _web_session(principal: AuthenticatedPrincipal) -> AuthenticatedWebSession:
    return AuthenticatedWebSession(session_id=principal.web_session_id, user_id=principal.user_id)


def _link_status(
    challenge_id: UUID,
    principal: AuthenticatedPrincipal,
    session: DatabaseSession,
    telegram: TelegramRuntime,
    *,
    status_code: int = 200,
    error: str | None = None,
    poll: bool = False,
) -> Response:
    status = GetTelegramLinkStatus(
        repository=SqlAlchemyTelegramLinkRepository(session), clock=telegram.clock
    ).execute(challenge_id=challenge_id, session=_web_session(principal))
    if status is None:
        raise HTTPException(status_code=404)
    if poll and status.state is TelegramLinkState.WAITING_FOR_TELEGRAM:
        # Nothing changed: no swap, so screen readers are not told "waiting" every two seconds.
        return Response(status_code=204, headers=PAGE_HEADERS)
    return render(
        "settings_link_status.html",
        {
            "challenge_id": str(challenge_id),
            "state": status.state.value,
            "states": TelegramLinkState,
            "display_name": status.telegram_display_name,
            "error": error,
        },
        status_code=status_code,
    )


def _challenge_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404) from None


@router.post(SETTINGS_PATH + "/telegram/link")
async def start_link(
    principal: WebMutationPrincipalDependency,
    session: DatabaseSession,
    telegram: TelegramDependency,
) -> Response:
    issued = IssueTelegramLinkChallenge(
        repository=SqlAlchemyTelegramLinkRepository(session),
        bot_username=telegram.bot_username,
        clock=telegram.clock,
    ).execute(session=_web_session(principal))
    return render(
        "settings_link.html",
        {
            "challenge_id": str(issued.challenge_id),
            "deep_link": issued.deep_link,
            "state": TelegramLinkState.WAITING_FOR_TELEGRAM.value,
            "states": TelegramLinkState,
            "display_name": None,
            "error": None,
        },
    )


@router.get(SETTINGS_PATH + "/telegram/link/{raw_id}")
async def link_status(
    raw_id: str,
    principal: PagePrincipalDependency,
    session: DatabaseSession,
    telegram: TelegramDependency,
) -> Response:
    return _link_status(_challenge_id(raw_id), principal, session, telegram, poll=True)


@router.post(SETTINGS_PATH + "/telegram/link/{raw_id}/confirm")
async def confirm_link(
    raw_id: str,
    principal: WebMutationPrincipalDependency,
    session: DatabaseSession,
    telegram: TelegramDependency,
) -> Response:
    challenge_id = _challenge_id(raw_id)
    connection = confirm_telegram_link(
        runtime=telegram,
        repository=SqlAlchemyTelegramLinkRepository(session),
        challenge_id=challenge_id,
        principal=principal,
    )
    if connection is None:
        return _link_status(
            challenge_id,
            principal,
            session,
            telegram,
            status_code=422,
            error=_("Telegram could not be connected. Please start again."),
        )
    return htmx_redirect(f"{SETTINGS_PATH}?done=connected")


@router.get(SETTINGS_PATH + "/telegram/disconnect")
async def disconnect_page(
    principal: PagePrincipalDependency,
    session: DatabaseSession,
    telegram: TelegramDependency,
) -> Response:
    user = _user(session, principal)
    view = _telegram_view(session, user, telegram)
    if not view.connected:
        raise HTTPException(status_code=404)
    return render("settings_disconnect.html", {"active": "settings", "telegram": view})


@router.post(SETTINGS_PATH + "/telegram/disconnect")
async def disconnect(
    principal: WebMutationPrincipalDependency,
    session: DatabaseSession,
    telegram: TelegramDependency,
) -> Response:
    UnlinkTelegram(
        repository=SqlAlchemyTelegramLinkRepository(session), clock=telegram.clock
    ).execute(user_id=principal.user_id)
    return htmx_redirect(f"{SETTINGS_PATH}?done=disconnected")
