"""The whole product loop (MVP sections 2 and 11; web design W10).

Capture in Telegram, understand on the Web: settings and linking on the Web, an expense typed
into the bot, the same expense in Web history and on the dashboard, a Web correction that
changes the dashboard, and the bot's recent list agreeing with the Web.
"""

from datetime import UTC, datetime, timedelta
from itertools import count
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
)
from mintflow.http.capture import CaptureRuntime
from mintflow.infrastructure.persistence.models import UserRecord, WebSessionRecord
from mintflow.main import create_app
from mintflow.telegram import InlineButton
from mintflow.telegram.runtime import TelegramRuntime
from mintflow.telegram.testing import RecordingTelegramBotApi
from mintflow.web.testing import parse_html

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"
SECRET = "L" * 43
TELEGRAM_USER_ID = 515_151
HOOK_SECRET = "loop-hook-secret"
NBSP = "\u00a0"


class Loop:
    """One signed-in Web user, the application, and the recording bot."""

    def __init__(self, database_url: str, session: Session) -> None:
        self.bot = RecordingTelegramBotApi()
        self.application = create_app(
            Settings(
                environment="test",
                log_level="CRITICAL",
                database_url=SecretStr(database_url),
                authentication_rate_limit_key=SecretStr("integration-rate-limit-key"),
                authentication_csrf_signing_key=SecretStr("integration-csrf-signing-key"),
                authentication_web_origin=ORIGIN,
                authentication_return_targets=frozenset({"dashboard"}),
                email_backend=None,
            )
        )
        state = self.application.state
        auth: AuthenticationRuntime = state.authentication_runtime
        state.authentication_runtime = AuthenticationRuntime(
            session_factory=auth.session_factory,
            email_sender=auth.email_sender,
            link_builder=auth.link_builder,
            rate_limit_digester=auth.rate_limit_digester,
            csrf_digester=auth.csrf_digester,
            clock=lambda: NOW,
        )
        state.capture_runtime = CaptureRuntime(clock=lambda: NOW)
        state.telegram_runtime = TelegramRuntime(
            bot_api=self.bot,
            bot_username="mintflow_loop_bot",
            webhook_secret=SecretStr(HOOK_SECRET),
            web_origin=ORIGIN,
            clock=lambda: NOW,
        )
        self._update_ids = count(90_000)
        user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW - timedelta(days=1))
        session.add(user)
        session.commit()
        session.add(
            WebSessionRecord(
                user_id=user.id,
                secret_hash=hash_token(SECRET),
                issued_at=NOW - timedelta(hours=1),
                expires_at=NOW + timedelta(days=1),
            )
        )
        session.commit()

    # --- the Web ------------------------------------------------------------------------------

    def web(self) -> AsyncClient:
        runtime: AuthenticationRuntime = self.application.state.authentication_runtime
        cookies = {
            AUTHENTICATED_SESSION_COOKIE_NAME: SECRET,
            CSRF_COOKIE_NAME: runtime.csrf_digester.derive(session_secret=SECRET),
        }
        transport = ASGITransport(app=self.application, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url=ORIGIN, cookies=cookies)

    @staticmethod
    def htmx(client: AsyncClient) -> dict[str, str]:
        return {
            "Origin": ORIGIN,
            "HX-Request": "true",
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
            CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME],
        }

    # --- Telegram -----------------------------------------------------------------------------

    async def _webhook(self, update: dict[str, object]) -> None:
        transport = ASGITransport(app=self.application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            response = await client.post(
                "/telegram/webhook",
                json={"update_id": next(self._update_ids), **update},
                headers={"X-Telegram-Bot-Api-Secret-Token": HOOK_SECRET},
            )
        assert response.status_code == 200

    async def say(self, text: str) -> str:
        await self._webhook(
            {
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "from": {"id": TELEGRAM_USER_ID, "is_bot": False, "first_name": "Ada"},
                    "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                    "text": text,
                }
            }
        )
        return self.last_text()

    async def press(self, label: str) -> str:
        await self._webhook(
            {
                "callback_query": {
                    "id": f"cb-{label}",
                    "from": {"id": TELEGRAM_USER_ID, "is_bot": False, "first_name": "Ada"},
                    "message": {
                        "message_id": 7,
                        "date": 0,
                        "text": "card",
                        "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                    },
                    "data": self._button(label),
                }
            }
        )
        return self.last_text()

    def _button(self, label: str) -> str:
        for call in reversed(self.bot.calls):
            keyboard = call.arguments.get("keyboard")
            for row in keyboard if isinstance(keyboard, tuple) else ():
                for button in row:
                    if isinstance(button, InlineButton) and button.text == label:
                        assert button.callback_data is not None
                        return button.callback_data
        raise AssertionError(f"no button {label!r}")

    def last_text(self) -> str:
        texts = [
            str(call.arguments["text"])
            for call in self.bot.calls
            if call.method in {"send_message", "edit_message_text"}
        ]
        return texts[-1] if texts else ""


@pytest.mark.anyio
async def test_capture_in_telegram_understand_on_the_web(
    db_session: Session, migrated_database_url: str
) -> None:
    loop = Loop(migrated_database_url, db_session)
    async with loop.web() as web:
        # 1. Conventions on the Web.
        saved = await web.post(
            "/settings/preferences",
            content=urlencode(
                {"timezone": "Europe/Warsaw", "default_currency": "EUR", "locale": ""}
            ),
            headers=loop.htmx(web),
        )
        assert saved.headers["hx-redirect"] == "/settings?done=saved"

        # 2. Link Telegram: the Web issues the link, the bot claims it, the Web confirms.
        started = parse_html(
            (await web.post("/settings/telegram/link", headers=loop.htmx(web))).text
        )
        deep_link = str(
            started.find("section", id="telegram").find("a", class_="button").attrs["href"]
        )
        payload = deep_link.rsplit("start=", 1)[1]
        await loop.say(f"/start {payload}")
        poll_url = str(started.find("div", id="link-status").attrs["hx-get"])
        confirm = parse_html((await web.get(poll_url, headers=loop.htmx(web))).text).find("button")
        linked = await web.post(str(confirm.attrs["hx-post"]), headers=loop.htmx(web))
        assert linked.headers["hx-redirect"] == "/settings?done=connected"

        # 3. Capture in Telegram.
        await loop.say("/add")
        await loop.say("12.50")
        await loop.say("Corner Shop")
        review = await loop.press("Groceries")
        assert "Amount: 12.50 EUR (default currency)" in review
        saved_message = await loop.press("Confirm")
        assert "12.50 EUR" in saved_message

        # 4. Understand on the Web.
        history = parse_html((await web.get("/expenses")).text)
        [row] = history.find("tbody", id="expense-rows").find_all("tr")
        cells = [cell.text for cell in row.find_all("td")]
        assert cells == ["Sep 23, 2026", "Corner Shop", "Groceries", f"12.50{NBSP}EUR"]
        dashboard = parse_html((await web.get("/dashboard")).text)
        assert dashboard.find("p", class_="answer").text == f"You spent 12.50{NBSP}EUR"

        # 5. Correct it on the Web; the dashboard follows.
        expense_url = str(row.find("a").attrs["href"])
        form = parse_html((await web.get(f"{expense_url}/edit")).text).find("form", id="edit-form")
        values = {
            str(field.attrs["name"]): str(field.attrs.get("value") or "")
            for field in form.find_all("input")
        }
        values.update(currency="EUR", category_key="groceries", note="", amount="20.00")
        edited = await web.post(
            f"{expense_url}/edit", content=urlencode(values), headers=loop.htmx(web)
        )
        assert edited.headers["hx-redirect"] == f"{expense_url}?done=saved"
        dashboard = parse_html((await web.get("/dashboard")).text)
        assert dashboard.find("p", class_="answer").text == f"You spent 20.00{NBSP}EUR"

    # 6. Telegram agrees with the Web.
    recent = await loop.say("/recent")
    assert "20.00 EUR" in recent and "Corner Shop" in recent
