import json

import pytest

from mintflow.telegram import TelegramUpdate, UpdateKind, parse_update

USER = {"id": 42, "is_bot": False, "first_name": "Ada", "username": "ada"}
PRIVATE = {"id": 42, "type": "private"}


def _message(text: str | None = "hello", **extra: object) -> dict[str, object]:
    message: dict[str, object] = {"message_id": 7, "from": USER, "chat": PRIVATE, "date": 0}
    if text is not None:
        message["text"] = text
    message.update(extra)
    return message


def _parse(payload: dict[str, object]) -> TelegramUpdate:
    update = parse_update(json.dumps(payload))
    assert update is not None
    return update


def test_private_text_message() -> None:
    update = _parse({"update_id": 1, "message": _message()})

    assert update.kind is UpdateKind.TEXT_MESSAGE
    assert update.is_private_chat is True
    assert update.sender is not None and update.sender.id == 42
    assert update.start_payload is None


@pytest.mark.parametrize(
    ("text", "payload"),
    [
        ("/start abc_DEF-123", "abc_DEF-123"),
        ("  /start   abc  ", "abc"),
        ("/start", None),
        ("/start   ", None),
        ("/started abc", None),
        ("/help", None),
    ],
)
def test_start_payload(text: str, payload: str | None) -> None:
    assert _parse({"update_id": 1, "message": _message(text)}).start_payload == payload


@pytest.mark.parametrize(
    "forward",
    [
        {"forward_origin": {"type": "user", "date": 0, "sender_user": USER}},
        {"forward_date": 1700000000},
    ],
)
def test_forwarded_start_has_no_payload(forward: dict[str, object]) -> None:
    update = _parse({"update_id": 1, "message": _message("/start abc", **forward)})

    assert update.start_payload is None


@pytest.mark.parametrize(
    "chat",
    [
        {"id": -100, "type": "group"},
        {"id": -100, "type": "supergroup"},
        {"id": -100, "type": "channel"},
        {"id": 99, "type": "private"},  # a private chat that is not the sender's
    ],
)
def test_non_private_chats_are_identified(chat: dict[str, object]) -> None:
    update = _parse({"update_id": 1, "message": _message(chat=chat)})

    assert update.is_private_chat is False


def test_callback_query() -> None:
    update = _parse(
        {
            "update_id": 2,
            "callback_query": {
                "id": "cb-1",
                "from": USER,
                "message": _message("review"),
                "data": "confirm",
            },
        }
    )

    assert update.kind is UpdateKind.CALLBACK
    assert update.is_private_chat is True
    assert update.callback_query is not None and update.callback_query.data == "confirm"


@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 3, "edited_message": _message()},
        {"update_id": 3, "message": _message(text=None, photo=[{"file_id": "x"}])},
        {"update_id": 3, "message": {**_message(), "from": {**USER, "is_bot": True}}},
        {"update_id": 3, "callback_query": {"id": "cb", "from": USER}},
        {"update_id": 3, "channel_post": {"message_id": 1, "chat": {"id": -1, "type": "channel"}}},
    ],
)
def test_unsupported_updates_are_ignorable(payload: dict[str, object]) -> None:
    assert _parse(payload).kind is UpdateKind.IGNORABLE


@pytest.mark.parametrize(
    "raw", [b"", b"not json", b"[]", b'{"message": {}}', b'{"update_id": "x"}']
)
def test_malformed_payloads_are_not_updates(raw: bytes) -> None:
    assert parse_update(raw) is None
