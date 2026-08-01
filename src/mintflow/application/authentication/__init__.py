from mintflow.application.authentication.email import NormalizedEmail, normalize_email
from mintflow.application.authentication.login_challenge import (
    GENERIC_MAGIC_LINK_REQUEST_RESULT,
    INVALID_MAGIC_LINK_CONSUMPTION_RESULT,
    AuthenticatedUserIdentity,
    ConsumeMagicLink,
    EmailDeliveryError,
    LoginChallenge,
    MagicLinkConsumptionResult,
    MagicLinkMessage,
    MagicLinkRequestResult,
    RequestMagicLink,
)
from mintflow.application.authentication.magic_link import MagicLinkBuilder
from mintflow.application.authentication.tokens import GeneratedToken, generate_token, hash_token

__all__ = [
    "GENERIC_MAGIC_LINK_REQUEST_RESULT",
    "INVALID_MAGIC_LINK_CONSUMPTION_RESULT",
    "AuthenticatedUserIdentity",
    "ConsumeMagicLink",
    "EmailDeliveryError",
    "GeneratedToken",
    "LoginChallenge",
    "MagicLinkConsumptionResult",
    "MagicLinkBuilder",
    "MagicLinkMessage",
    "MagicLinkRequestResult",
    "NormalizedEmail",
    "RequestMagicLink",
    "generate_token",
    "hash_token",
    "normalize_email",
]
