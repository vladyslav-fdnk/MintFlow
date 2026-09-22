from mintflow.domain.capture import CaptureDraftState, CaptureSource, DraftFieldSource


def test_capture_source_members_and_values() -> None:
    assert {member.value for member in CaptureSource} == {
        "telegram_manual",
        "telegram_receipt",
        "web_manual",
    }
    assert CaptureSource.WEB_MANUAL == "web_manual"


def test_capture_draft_state_members_and_values() -> None:
    assert {member.value for member in CaptureDraftState} == {
        "collecting",
        "awaiting_recognition",
        "ready_for_review",
        "confirmed",
        "cancelled",
        "expired",
    }


def test_draft_field_source_members_and_values() -> None:
    assert {member.value for member in DraftFieldSource} == {
        "recognition",
        "user",
        "default",
    }
