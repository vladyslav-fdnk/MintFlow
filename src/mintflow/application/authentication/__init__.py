from mintflow.application.authentication.email import NormalizedEmail, normalize_email
from mintflow.application.authentication.login_challenge import (
    GENERIC_MAGIC_LINK_REQUEST_RESULT,
    EmailDeliveryError,
    LoginChallenge,
    MagicLinkMessage,
    MagicLinkRequestResult,
    RequestMagicLink,
)
from mintflow.application.authentication.magic_link import MagicLinkBuilder
from mintflow.application.authentication.tokens import GeneratedToken, generate_token, hash_token

__all__ = [
    "GENERIC_MAGIC_LINK_REQUEST_RESULT",
    "EmailDeliveryError",
    "GeneratedToken",
    "LoginChallenge",
    "MagicLinkBuilder",
    "MagicLinkMessage",
    "MagicLinkRequestResult",
    "NormalizedEmail",
    "RequestMagicLink",
    "generate_token",
    "hash_token",
    "normalize_email",
]
