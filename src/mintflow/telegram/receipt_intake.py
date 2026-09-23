"""Which Telegram messages are acceptable receipts (receipt_recognition_design.md, R5).

Only metadata is checked here; the worker checks the downloaded bytes again.
"""

from dataclasses import dataclass
from enum import StrEnum

from mintflow.application.receipts import ACCEPTED_RECEIPT_MEDIA_TYPES, MAX_RECEIPT_IMAGE_BYTES
from mintflow.telegram.updates import TelegramMessage


class ReceiptRejection(StrEnum):
    ALBUM = "album"
    UNSUPPORTED = "unsupported"
    TOO_LARGE = "too_large"


@dataclass(frozen=True, slots=True)
class ReceiptUpload:
    file_id: str


def receipt_upload(message: TelegramMessage) -> ReceiptUpload | ReceiptRejection:
    if message.media_group_id is not None:
        return ReceiptRejection.ALBUM
    if message.photo:
        # Telegram lists sizes smallest first; the largest reads best.
        largest = max(
            message.photo, key=lambda size: (size.width * size.height, size.file_size or 0)
        )
        if (largest.file_size or 0) > MAX_RECEIPT_IMAGE_BYTES:
            return ReceiptRejection.TOO_LARGE
        return ReceiptUpload(file_id=largest.file_id)
    document = message.document
    if document is None or document.mime_type not in ACCEPTED_RECEIPT_MEDIA_TYPES:
        return ReceiptRejection.UNSUPPORTED
    if (document.file_size or 0) > MAX_RECEIPT_IMAGE_BYTES:
        return ReceiptRejection.TOO_LARGE
    return ReceiptUpload(file_id=document.file_id)
