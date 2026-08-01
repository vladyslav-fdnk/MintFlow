import pytest

from mintflow.application.authentication.email import InvalidEmailError, normalize_email


def test_normalizes_domain_and_preserves_display_form() -> None:
    email = normalize_email("  Person@EXAMPLE.COM  ")

    assert email.canonical_email == "Person@example.com"
    assert email.display_email == "Person@EXAMPLE.COM"


def test_rejects_invalid_email_without_deliverability_lookup() -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email("not-an-email")


@pytest.mark.parametrize(
    "submitted_email",
    [
        "first.last+receipts@gmail.com",
        "first.last+receipts@example.com",
    ],
)
def test_does_not_apply_provider_specific_rewrites(submitted_email: str) -> None:
    email = normalize_email(submitted_email)

    assert email.canonical_email.startswith("first.last+receipts@")
