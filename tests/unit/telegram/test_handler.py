import logging
from datetime import UTC, datetime

import pytest

from mintflow.domain.user import User
from mintflow.telegram import TelegramUpdate, messages
from mintflow.telegram.handler import TelegramUpdateHandler
from mintflow.telegram.outgoing import CallbackAnswer, Outgoing, Reply
from mintflow.telegram.receipt_intake import ReceiptUpload
from mintflow.telegram.testing import RecordingTelegramBotApi

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SENDER_ID = 4242
WEB_ORIGIN = "https://app.mintflow.test"


class Events:
    """One ordered log shared by the fakes, to check commit-before-send ordering."""

    def __init__(self) -> None:
        self.log: list[str] = []


class FakeLedger:
    def __init__(self, events: Events, *, seen: set[int] | None = None) -> None:
        self.events = events
        self.seen = seen if seen is not None else set()

    def begin(self, *, update_id: int, now: datetime) -> bool:
        self.events.log.append(f"begin:{update_id}")
        if update_id in self.seen:
            return False
        self.seen.add(update_id)
        return True

    def commit(self) -> None:
        self.events.log.append("commit")

    def rollback(self) -> None:
        self.events.log.append("rollback")


class FakeResolver:
    def __init__(self, user: User | None) -> None:
        self.user = user
        self.calls: list[int] = []

    def execute(self, *, telegram_user_id: int) -> User | None:
        self.calls.append(telegram_user_id)
        return self.user


class FakeClaimer:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[str, int, str | None]] = []

    def execute(self, *, payload: str, telegram_user_id: int, display_name: str | None) -> bool:
        self.calls.append((payload, telegram_user_id, display_name))
        return self.result


class FakeCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def on_command(self, user: User, chat_id: int, command: str) -> list[Outgoing] | None:
        self.calls.append(("command", command))
        return [Reply(chat_id, "capture command")] if command == "/add" else None

    def on_text(self, user: User, chat_id: int, text: str) -> list[Outgoing]:
        self.calls.append(("text", text))
        return [Reply(chat_id, "capture text")]

    def on_media(self, user: User, chat_id: int, upload: ReceiptUpload) -> list[Outgoing]:
        self.calls.append(("media", upload.file_id))
        return [Reply(chat_id, "capture media")]

    def on_callback(
        self, user: User, chat_id: int, message_id: int, callback_query_id: str, data: str
    ) -> list[Outgoing] | None:
        self.calls.append(("callback", data))
        return [CallbackAnswer(callback_query_id)] if data.startswith("d:") else None


class EventBot(RecordingTelegramBotApi):
    events: Events | None = None

    def _record(self, method: str, **arguments: object) -> None:
        if self.events is not None:
            self.events.log.append(f"send:{method}")
        super()._record(method, **arguments)


class Harness:
    def __init__(self, *, linked: bool = False, claim_result: bool = True) -> None:
        self.events = Events()
        self.ledger = FakeLedger(self.events)
        self.resolver = FakeResolver(User.create(now=NOW) if linked else None)
        self.claimer = FakeClaimer(claim_result)
        self.capture = FakeCapture()
        self.bot = EventBot()
        self.bot.events = self.events
        self.handler = TelegramUpdateHandler(
            ledger=self.ledger,
            resolve_user=self.resolver,
            claim_link=self.claimer,
            capture=self.capture,
            bot_api=self.bot,
            web_origin=WEB_ORIGIN,
            clock=lambda: NOW,
        )

    def texts(self) -> list[str]:
        return [str(call.arguments["text"]) for call in self.bot.calls_to("send_message")]


def _message(
    text: str, *, update_id: int = 1, chat: dict[str, object] | None = None, **extra: object
) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": 10,
                "from": {"id": SENDER_ID, "is_bot": False, "first_name": "Ada", "username": "ada"},
                "chat": chat or {"id": SENDER_ID, "type": "private"},
                "date": 0,
                "text": text,
                **extra,
            },
        }
    )


def _callback(data: str = "confirm", *, update_id: int = 2) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": "cb-1",
                "from": {"id": SENDER_ID, "is_bot": False, "first_name": "Ada"},
                "message": {
                    "message_id": 11,
                    "chat": {"id": SENDER_ID, "type": "private"},
                    "date": 0,
                    "text": "card",
                },
                "data": data,
            },
        }
    )


def test_start_with_a_token_claims_for_the_verified_sender() -> None:
    harness = Harness()

    harness.handler.handle(_message("/start token_ABC-123"))

    assert harness.claimer.calls == [("token_ABC-123", SENDER_ID, "Ada (@ada)")]
    assert harness.texts() == [messages.LINK_CLAIMED]


def test_a_failed_claim_gets_the_generic_restart_instruction() -> None:
    harness = Harness(claim_result=False)

    harness.handler.handle(_message("/start stale-token"))

    assert harness.texts() == [messages.LINK_FAILED]


def test_a_forwarded_start_link_is_never_claimed() -> None:
    harness = Harness()

    harness.handler.handle(_message("/start token", forward_date=1700000000))

    assert harness.claimer.calls == []
    assert harness.texts() == [messages.WELCOME_UNLINKED]


@pytest.mark.parametrize(
    ("linked", "expected"), [(False, messages.WELCOME_UNLINKED), (True, messages.WELCOME_LINKED)]
)
def test_start_without_a_token_welcomes(linked: bool, expected: str) -> None:
    harness = Harness(linked=linked)

    harness.handler.handle(_message("/start"))

    assert harness.texts() == [expected]


@pytest.mark.parametrize("text", ["/help", "/help@mintflow_bot", "/HELP extra"])
def test_help_works_for_everyone_and_links_the_web(text: str) -> None:
    harness = Harness()

    harness.handler.handle(_message(text))

    [reply] = harness.texts()
    assert reply == messages.help_text(web_origin=WEB_ORIGIN)
    assert WEB_ORIGIN in reply


@pytest.mark.parametrize("text", ["12.50 coffee", "/add", "/recent", "hello"])
def test_unlinked_users_are_refused_and_nothing_is_resolved_further(text: str) -> None:
    harness = Harness()

    harness.handler.handle(_message(text))

    assert harness.texts() == [messages.NOT_LINKED]
    assert harness.claimer.calls == []


def test_linked_free_text_goes_to_the_capture_flow() -> None:
    harness = Harness(linked=True)

    harness.handler.handle(_message("12.50 coffee"))

    assert harness.capture.calls == [("text", "12.50 coffee")]
    assert harness.texts() == ["capture text"]


def test_linked_commands_go_to_the_capture_flow_first() -> None:
    harness = Harness(linked=True)

    harness.handler.handle(_message("/add@mintflow_bot", update_id=1))
    harness.handler.handle(_message("/unknown", update_id=2))

    assert harness.capture.calls == [("command", "/add"), ("command", "/unknown")]
    assert harness.texts() == ["capture command", messages.UNKNOWN_INPUT]


def test_linked_callbacks_go_to_the_capture_flow() -> None:
    harness = Harness(linked=True)

    harness.handler.handle(_callback("d:abc:confirm", update_id=3))
    harness.handler.handle(_callback("something-else", update_id=4))

    assert harness.capture.calls == [("callback", "d:abc:confirm"), ("callback", "something-else")]
    answers = harness.bot.calls_to("answer_callback_query")
    assert [answer.arguments["text"] for answer in answers] == [None, messages.UNKNOWN_INPUT]


def test_unlinked_users_never_reach_the_capture_flow() -> None:
    harness = Harness()

    harness.handler.handle(_message("/add", update_id=1))
    harness.handler.handle(_message("12.50", update_id=2))
    harness.handler.handle(_callback("d:abc:confirm", update_id=3))

    assert harness.capture.calls == []


def test_unlinked_callbacks_are_answered_and_refused() -> None:
    harness = Harness()

    harness.handler.handle(_callback())

    [answer] = harness.bot.calls_to("answer_callback_query")
    assert answer.arguments["callback_query_id"] == "cb-1"
    assert harness.texts() == [messages.NOT_LINKED]


@pytest.mark.parametrize(
    "update",
    [
        _message("/start token", chat={"id": -100, "type": "group"}),
        _message("/start token", chat={"id": -100, "type": "supergroup"}),
        _message("/start token", chat={"id": 99, "type": "private"}),
        TelegramUpdate.model_validate({"update_id": 5, "edited_message": {}}),
    ],
)
def test_non_private_and_unsupported_updates_touch_nothing(update: TelegramUpdate) -> None:
    harness = Harness()

    harness.handler.handle(update)

    assert harness.events.log == []
    assert harness.claimer.calls == []
    assert harness.bot.calls == []


def test_a_redelivered_update_does_its_work_once() -> None:
    harness = Harness()
    update = _message("/start token")

    harness.handler.handle(update)
    harness.handler.handle(update)

    assert len(harness.claimer.calls) == 1
    assert harness.texts() == [messages.LINK_CLAIMED]
    assert harness.events.log[-2:] == ["begin:1", "rollback"]


def test_replies_are_sent_only_after_commit() -> None:
    harness = Harness()

    harness.handler.handle(_message("/start token"))

    assert harness.events.log == ["begin:1", "commit", "send:send_message"]


def test_a_failure_while_handling_rolls_back_and_sends_nothing() -> None:
    harness = Harness()

    def explode(**_: object) -> bool:
        raise RuntimeError("database unavailable")

    harness.claimer.execute = explode  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        harness.handler.handle(_message("/start token"))

    assert harness.events.log == ["begin:1", "rollback"]
    assert harness.bot.calls == []


def test_a_failed_send_is_logged_without_content_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness()
    harness.bot.fail_methods.add("send_message")

    with caplog.at_level(logging.WARNING, logger="mintflow.telegram.handler"):
        harness.handler.handle(_message("/start token"))

    assert "telegram_reply_failed method=send_message" in caplog.text
    assert messages.LINK_CLAIMED not in caplog.text
    assert "token" not in caplog.text.replace("telegram_reply_failed", "")


def _photo(update_id: int = 7, **extra: object) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": 12,
                "from": {"id": SENDER_ID, "is_bot": False, "first_name": "Ada"},
                "chat": {"id": SENDER_ID, "type": "private"},
                "date": 0,
                "photo": [{"file_id": "photo-file", "width": 800, "height": 600}],
                **extra,
            },
        }
    )


def test_linked_receipt_photos_go_to_the_capture_flow() -> None:
    harness = Harness(linked=True)

    harness.handler.handle(_photo())

    assert harness.capture.calls == [("media", "photo-file")]
    assert harness.texts() == ["capture media"]


def test_unlinked_receipt_photos_are_refused_and_stored_nowhere() -> None:
    harness = Harness()

    harness.handler.handle(_photo())

    assert harness.capture.calls == []
    assert harness.texts() == [messages.NOT_LINKED]


def test_unusable_receipts_get_an_instruction_and_reach_no_flow() -> None:
    harness = Harness(linked=True)

    harness.handler.handle(_photo(media_group_id="album-1"))

    assert harness.capture.calls == []
    assert harness.texts() == [messages.RECEIPT_ALBUM]
