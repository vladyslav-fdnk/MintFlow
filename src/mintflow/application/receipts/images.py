"""What a stored receipt image may be (receipt_recognition_design.md, R3, R5)."""

from typing import Final

MAX_RECEIPT_IMAGE_BYTES: Final = 10 * 1024 * 1024
ACCEPTED_RECEIPT_MEDIA_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp"})


def sniff_media_type(content: bytes) -> str | None:
    """The accepted media type the bytes actually are, or None; never trusts a declared type."""
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None
