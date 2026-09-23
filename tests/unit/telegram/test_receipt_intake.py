import httpx
import pytest
from pydantic import SecretStr

from mintflow.application.receipts import MAX_RECEIPT_IMAGE_BYTES, sniff_media_type
from mintflow.telegram import HttpxTelegramBotApi, TelegramApiError, TelegramMessage
from mintflow.telegram.receipt_intake import ReceiptRejection, ReceiptUpload, receipt_upload

TOKEN = "123456:very-secret-token"


def _message(**media: object) -> TelegramMessage:
    return TelegramMessage.model_validate(
        {
            "message_id": 1,
            "from": {"id": 5, "is_bot": False, "first_name": "A"},
            "chat": {"id": 5, "type": "private"},
            **media,
        }
    )


def test_the_largest_photo_size_is_used() -> None:
    message = _message(
        photo=[
            {"file_id": "small", "width": 90, "height": 160, "file_size": 2_000},
            {"file_id": "large", "width": 1280, "height": 720, "file_size": 180_000},
            {"file_id": "medium", "width": 320, "height": 568, "file_size": 20_000},
        ]
    )

    assert receipt_upload(message) == ReceiptUpload(file_id="large")


@pytest.mark.parametrize("mime_type", ["image/jpeg", "image/png", "image/webp"])
def test_image_documents_are_accepted(mime_type: str) -> None:
    message = _message(document={"file_id": "doc", "mime_type": mime_type, "file_size": 50_000})

    assert receipt_upload(message) == ReceiptUpload(file_id="doc")


@pytest.mark.parametrize(
    ("media", "rejection"),
    [
        pytest.param(
            {"photo": [{"file_id": "p", "width": 10, "height": 10}], "media_group_id": "album"},
            ReceiptRejection.ALBUM,
            id="album",
        ),
        pytest.param(
            {"document": {"file_id": "d", "mime_type": "application/pdf"}},
            ReceiptRejection.UNSUPPORTED,
            id="pdf",
        ),
        pytest.param(
            {"document": {"file_id": "d", "mime_type": "image/heic"}},
            ReceiptRejection.UNSUPPORTED,
            id="heic",
        ),
        pytest.param({"document": {"file_id": "d"}}, ReceiptRejection.UNSUPPORTED, id="no mime"),
        pytest.param(
            {
                "document": {
                    "file_id": "d",
                    "mime_type": "image/png",
                    "file_size": MAX_RECEIPT_IMAGE_BYTES + 1,
                }
            },
            ReceiptRejection.TOO_LARGE,
            id="large document",
        ),
        pytest.param(
            {
                "photo": [
                    {
                        "file_id": "p",
                        "width": 5000,
                        "height": 5000,
                        "file_size": MAX_RECEIPT_IMAGE_BYTES + 1,
                    }
                ]
            },
            ReceiptRejection.TOO_LARGE,
            id="large photo",
        ),
    ],
)
def test_unusable_receipts_are_rejected(
    media: dict[str, object], rejection: ReceiptRejection
) -> None:
    assert receipt_upload(_message(**media)) is rejection


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"%PDF-1.7", None),
        (b"GIF89a", None),
        (b"", None),
    ],
)
def test_media_type_comes_from_the_bytes(content: bytes, media_type: str | None) -> None:
    assert sniff_media_type(content) == media_type


def _api(handler: httpx.MockTransport) -> HttpxTelegramBotApi:
    return HttpxTelegramBotApi(token=SecretStr(TOKEN), client=httpx.Client(transport=handler))


def test_get_file_and_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "photos/a.jpg", "file_size": 4}}
            )
        assert request.url.path == f"/file/bot{TOKEN}/photos/a.jpg"
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0")

    api = _api(httpx.MockTransport(handler))

    telegram_file = api.get_file(file_id="abc")
    assert (telegram_file.file_path, telegram_file.file_size) == ("photos/a.jpg", 4)
    assert api.download_file(file_path=telegram_file.file_path, max_bytes=10) == b"\xff\xd8\xff\xe0"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * 11),
        httpx.Response(404, content=b"not found"),
    ],
)
def test_download_refuses_oversized_or_failed_files_without_leaking_the_token(
    response: httpx.Response,
) -> None:
    api = _api(httpx.MockTransport(lambda request: response))

    with pytest.raises(TelegramApiError) as error:
        api.download_file(file_path="photos/a.jpg", max_bytes=10)

    assert TOKEN not in str(error.value) and TOKEN not in repr(error.value)


def test_download_transport_errors_do_not_leak_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(TelegramApiError) as error:
        _api(httpx.MockTransport(handler)).download_file(file_path="photos/a.jpg", max_bytes=10)

    assert error.value.__cause__ is None and error.value.__suppress_context__
