import json

import httpx
import pytest
from pydantic import SecretStr

from mintflow.telegram import HttpxTelegramBotApi, InlineButton, TelegramApiError, UpdateKind

TOKEN = "123456:very-secret-token"


def _api(handler: httpx.MockTransport) -> HttpxTelegramBotApi:
    return HttpxTelegramBotApi(token=SecretStr(TOKEN), client=httpx.Client(transport=handler))


def _ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def test_send_message_posts_text_and_keyboard_to_the_method_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok({"message_id": 55, "chat": {"id": 42, "type": "private"}})

    sent = _api(httpx.MockTransport(handler)).send_message(
        chat_id=42,
        text="Hello",
        keyboard=(
            (InlineButton("Confirm", callback_data="confirm"),),
            (InlineButton("Open Web", url="https://app.mintflow.test"),),
        ),
    )

    assert (sent.chat_id, sent.message_id) == (42, 55)
    [request] = seen
    assert request.method == "POST"
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": 42,
        "text": "Hello",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "Confirm", "callback_data": "confirm"}],
                [{"text": "Open Web", "url": "https://app.mintflow.test"}],
            ]
        },
    }


def test_set_webhook_sends_the_secret_and_limits_update_kinds() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok(True)

    _api(httpx.MockTransport(handler)).set_webhook(
        url="https://api.mintflow.test/telegram/webhook", secret_token=SecretStr("hook-secret")
    )

    assert seen == [
        {
            "url": "https://api.mintflow.test/telegram/webhook",
            "secret_token": "hook-secret",
            "allowed_updates": ["message", "callback_query"],
        }
    ]


def _assert_no_token(error: BaseException) -> None:
    assert TOKEN not in str(error)
    assert TOKEN not in repr(error)
    assert error.__cause__ is None
    # Either no original exception at all, or one hidden with "from None".
    assert error.__context__ is None or error.__suppress_context__ is True


def test_api_errors_carry_the_method_and_code_but_never_the_token() -> None:
    api = _api(
        httpx.MockTransport(
            lambda request: httpx.Response(
                400, json={"ok": False, "error_code": 400, "description": "Bad Request"}
            )
        )
    )

    with pytest.raises(TelegramApiError) as error:
        api.answer_callback_query(callback_query_id="cb")

    assert (error.value.method, error.value.error_code) == ("answerCallbackQuery", 400)
    _assert_no_token(error.value)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_transport_failures_do_not_leak_the_token(failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(TelegramApiError) as error:
        _api(httpx.MockTransport(handler)).delete_webhook()

    _assert_no_token(error.value)


def test_non_json_responses_are_api_errors() -> None:
    api = _api(httpx.MockTransport(lambda request: httpx.Response(502, text="<html>")))

    with pytest.raises(TelegramApiError) as error:
        api.send_message(chat_id=1, text="x")

    _assert_no_token(error.value)


def test_get_updates_keeps_the_id_of_an_unreadable_update() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["offset"] == 10
        return _ok(
            [
                {
                    "update_id": 10,
                    "message": {"message_id": 1, "chat": {"id": 1, "type": "private"}},
                },
                {"update_id": 11, "message": "not an object"},
                {"no_update_id": True},
            ]
        )

    updates = _api(httpx.MockTransport(handler)).get_updates(offset=10, timeout_seconds=1)

    assert [update.update_id for update in updates] == [10, 11]
    assert updates[1].kind is UpdateKind.IGNORABLE


def test_repr_hides_the_token() -> None:
    api = _api(httpx.MockTransport(lambda request: _ok(True)))

    assert TOKEN not in repr(api)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"text": "x"}, "exactly one"),
        ({"text": "x", "callback_data": "a", "url": "https://x.test"}, "exactly one"),
        ({"text": "x", "callback_data": "é" * 33}, "64 bytes"),
    ],
)
def test_inline_button_validation(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        InlineButton(**kwargs)


@pytest.mark.parametrize("text", ["", "x" * 4097])
def test_message_text_length_is_enforced_before_sending(text: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _ok(True)

    api = _api(httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="characters"):
        api.send_message(chat_id=1, text=text)
    assert calls == []
