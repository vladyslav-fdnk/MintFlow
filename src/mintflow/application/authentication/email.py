from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email


class InvalidEmailError(ValueError):
    """Raised internally when a submitted address is not syntactically valid."""


@dataclass(frozen=True, slots=True)
class NormalizedEmail:
    canonical_email: str
    display_email: str


def normalize_email(submitted_email: str) -> NormalizedEmail:
    """Validate an address without DNS checks and derive storage/presentation forms."""
    display_email = submitted_email.strip()
    try:
        validated = validate_email(display_email, check_deliverability=False)
    except EmailNotValidError as error:
        raise InvalidEmailError("invalid email address") from error

    return NormalizedEmail(
        canonical_email=validated.normalized,
        display_email=display_email,
    )
