from enum import StrEnum


class CaptureSource(StrEnum):
    """The channel and method that originated a CaptureDraft.

    Only WEB_MANUAL is reachable by any code in the capture-foundation
    sprint. The Telegram values are reserved so a stored source's meaning
    never has to change once the Telegram capture sprint lands.
    """

    TELEGRAM_MANUAL = "telegram_manual"
    TELEGRAM_RECEIPT = "telegram_receipt"
    WEB_MANUAL = "web_manual"


class CaptureDraftState(StrEnum):
    """CaptureDraft lifecycle state.

    AWAITING_RECOGNITION is reserved for the receipt-capture sprint and
    must not be produced or accepted by any code added in this sprint.
    """

    COLLECTING = "collecting"
    AWAITING_RECOGNITION = "awaiting_recognition"
    READY_FOR_REVIEW = "ready_for_review"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DraftFieldSource(StrEnum):
    """Provenance of a value set on a CaptureDraft field.

    RECOGNITION is reserved for the receipt-capture sprint.
    """

    RECOGNITION = "recognition"
    USER = "user"
    DEFAULT = "default"
