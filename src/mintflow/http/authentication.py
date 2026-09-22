import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Annotated
from urllib.parse import parse_qs, quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from mintflow.application.authentication import (
    AuthenticateWebSession,
    ConsumeMagicLink,
    CsrfTokenDigester,
    LogoutWebSession,
    MagicLinkBuilder,
    RequestMagicLink,
    RevokeAllWebSessions,
    RevokeWebSession,
    generate_token,
)
from mintflow.application.authentication.login_challenge import (
    GENERIC_MAGIC_LINK_REQUEST_RESULT,
    EmailSender,
)
from mintflow.application.authentication.magic_link import InvalidReturnTargetError
from mintflow.application.authentication.rate_limit import RateLimitDigester
from mintflow.config import Settings
from mintflow.infrastructure.persistence import (
    PostgreSQLAuthenticationRateLimiter,
    SqlAlchemyAuthenticationAuditAppender,
    SqlAlchemyLoginChallengeConsumer,
    SqlAlchemyLoginChallengeStore,
    SqlAlchemyWebSessionRepository,
)

AUTHENTICATION_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
GENERIC_AUTHENTICATION_ERROR_MESSAGE = "The authentication request could not be completed."
UNKNOWN_NETWORK_SOURCE = "unknown"
MAX_MAGIC_LINK_REQUEST_BODY_BYTES = 1_024
MAX_MAGIC_LINK_CONFIRMATION_BODY_BYTES = 1_024
MAX_SUBMITTED_EMAIL_LENGTH = 320
MAX_RETURN_TARGET_LENGTH = 64
MAGIC_LINK_CONFIRMATION_PATH = "/auth/magic-link"
MAGIC_LINK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
AUTHENTICATED_SESSION_COOKIE_NAME = "__Host-mintflow_session"
AUTHENTICATED_SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
GENERIC_UNAUTHENTICATED_MESSAGE = "Authentication required."
CSRF_COOKIE_NAME = "__Host-mintflow_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
GENERIC_CSRF_REJECTED_MESSAGE = "The request could not be verified."
GENERIC_LOGOUT_RESULT_MESSAGE = "Signed out."

router = APIRouter(prefix="/auth", tags=["authentication"])


class AuthenticationConfigurationError(RuntimeError):
    """A secret-safe failure at the authentication composition boundary."""


@dataclass(frozen=True, slots=True)
class NormalizedNetworkSource:
    value: str


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: UUID
    web_session_id: UUID


class MagicLinkRequestDTO(BaseModel):
    """The bounded public representation of a Magic Link request."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=MAX_SUBMITTED_EMAIL_LENGTH)
    return_target: str = Field(min_length=1, max_length=MAX_RETURN_TARGET_LENGTH)


@dataclass(frozen=True, slots=True)
class MagicLinkConfirmationDTO:
    token: str


@dataclass(frozen=True, slots=True)
class AuthenticationUseCases:
    request_magic_link: RequestMagicLink
    consume_magic_link: ConsumeMagicLink
    authenticate_web_session: AuthenticateWebSession
    revoke_web_session: RevokeWebSession
    revoke_all_web_sessions: RevokeAllWebSessions


@dataclass(frozen=True, slots=True)
class AuthenticationRuntime:
    session_factory: sessionmaker[Session]
    email_sender: EmailSender | None
    link_builder: MagicLinkBuilder
    rate_limit_digester: RateLimitDigester
    csrf_digester: CsrfTokenDigester
    clock: Callable[[], datetime]


def build_authentication_runtime(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    email_sender: EmailSender | None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AuthenticationRuntime:
    """Validate and retain process-scoped collaborators without exposing secret values."""
    try:
        return AuthenticationRuntime(
            session_factory=session_factory,
            email_sender=email_sender,
            link_builder=MagicLinkBuilder(
                web_origin=settings.authentication_web_origin,
                allowed_return_targets=settings.authentication_return_targets,
            ),
            rate_limit_digester=RateLimitDigester(
                settings.authentication_rate_limit_key.get_secret_value().encode()
            ),
            csrf_digester=CsrfTokenDigester(
                settings.authentication_csrf_signing_key.get_secret_value().encode()
            ),
            clock=clock,
        )
    except (TypeError, ValueError):
        raise AuthenticationConfigurationError(
            "authentication runtime configuration is invalid"
        ) from None


async def get_database_session(request: Request) -> AsyncIterator[Session]:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    session = runtime.session_factory()
    try:
        yield session
    finally:
        session.close()


DatabaseSession = Annotated[Session, Depends(get_database_session)]


async def get_authentication_use_cases(
    request: Request,
    session: DatabaseSession,
) -> AuthenticationUseCases:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    if runtime.email_sender is None:
        raise AuthenticationConfigurationError(
            "authentication runtime configuration is invalid"
        ) from None
    web_sessions = SqlAlchemyWebSessionRepository(session)
    return AuthenticationUseCases(
        request_magic_link=RequestMagicLink(
            challenge_store=SqlAlchemyLoginChallengeStore(session),
            email_sender=runtime.email_sender,
            link_builder=runtime.link_builder,
            rate_limiter=PostgreSQLAuthenticationRateLimiter(session),
            rate_limit_digester=runtime.rate_limit_digester,
            audit_appender=SqlAlchemyAuthenticationAuditAppender(session),
            clock=runtime.clock,
            token_generator=generate_token,
        ),
        consume_magic_link=ConsumeMagicLink(
            challenge_consumer=SqlAlchemyLoginChallengeConsumer(session),
            clock=runtime.clock,
            token_generator=generate_token,
        ),
        authenticate_web_session=AuthenticateWebSession(
            repository=web_sessions,
            clock=runtime.clock,
        ),
        revoke_web_session=RevokeWebSession(repository=web_sessions, clock=runtime.clock),
        revoke_all_web_sessions=RevokeAllWebSessions(
            repository=web_sessions,
            clock=runtime.clock,
        ),
    )


AuthenticationUseCasesDependency = Annotated[
    AuthenticationUseCases, Depends(get_authentication_use_cases)
]


async def get_consume_magic_link(
    request: Request,
    session: DatabaseSession,
) -> ConsumeMagicLink:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    return ConsumeMagicLink(
        challenge_consumer=SqlAlchemyLoginChallengeConsumer(session),
        clock=runtime.clock,
        token_generator=generate_token,
    )


ConsumeMagicLinkDependency = Annotated[ConsumeMagicLink, Depends(get_consume_magic_link)]


async def get_authenticate_web_session(
    request: Request,
    session: DatabaseSession,
) -> AuthenticateWebSession:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    return AuthenticateWebSession(
        repository=SqlAlchemyWebSessionRepository(session),
        clock=runtime.clock,
    )


AuthenticateWebSessionDependency = Annotated[
    AuthenticateWebSession, Depends(get_authenticate_web_session)
]


async def get_logout_web_session(
    request: Request,
    session: DatabaseSession,
) -> LogoutWebSession:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    return LogoutWebSession(
        repository=SqlAlchemyWebSessionRepository(session),
        clock=runtime.clock,
    )


LogoutWebSessionDependency = Annotated[LogoutWebSession, Depends(get_logout_web_session)]


async def get_authenticated_principal(
    request: Request,
    authenticate: AuthenticateWebSessionDependency,
) -> AuthenticatedPrincipal:
    session_secret = request.cookies.get(AUTHENTICATED_SESSION_COOKIE_NAME)
    if session_secret is None or MAGIC_LINK_TOKEN_PATTERN.fullmatch(session_secret) is None:
        raise _unauthenticated()
    authenticated = authenticate.execute(secret=session_secret)
    if authenticated is None:
        raise _unauthenticated()
    return AuthenticatedPrincipal(
        user_id=authenticated.user_id,
        web_session_id=authenticated.session_id,
    )


AuthenticatedPrincipalDependency = Annotated[
    AuthenticatedPrincipal, Depends(get_authenticated_principal)
]


async def _verified_csrf_session_secret(request: Request) -> str:
    """Validate Origin and a session-bound CSRF token, and return the raw session secret.

    This intentionally does not require the session to currently authenticate:
    the CSRF token is a stateless derivation of the raw secret, so it still
    matches a secret whose server-side session has since been revoked or has
    expired. Callers that need full authentication additionally depend on
    ``AuthenticatedPrincipalDependency``.
    """
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    if not has_approved_login_origin(
        request=request, approved_origin=runtime.link_builder.web_origin
    ):
        raise _csrf_rejected()
    session_secret = request.cookies.get(AUTHENTICATED_SESSION_COOKIE_NAME)
    candidate = request.headers.get(CSRF_HEADER_NAME)
    if (
        session_secret is None
        or candidate is None
        or not runtime.csrf_digester.matches(session_secret=session_secret, candidate=candidate)
    ):
        raise _csrf_rejected()
    return session_secret


async def get_csrf_protected_principal(
    request: Request,
    principal: AuthenticatedPrincipalDependency,
) -> AuthenticatedPrincipal:
    """Require an authenticated session plus a matching session-bound CSRF token.

    Resolving ``principal`` first means a missing, revoked, expired, or
    deactivated-user session already fails with the generic unauthenticated
    response before any CSRF check runs.
    """
    await _verified_csrf_session_secret(request)
    return principal


CsrfProtectedPrincipalDependency = Annotated[
    AuthenticatedPrincipal, Depends(get_csrf_protected_principal)
]


async def get_csrf_verified_session_secret(request: Request) -> str:
    """CSRF/Origin protection without requiring the session to still authenticate.

    Used only by logout, which must still validate CSRF and clear the
    browser cookie for a session that is already revoked or expired,
    without disclosing that distinction.
    """
    return await _verified_csrf_session_secret(request)


CsrfVerifiedSessionSecretDependency = Annotated[str, Depends(get_csrf_verified_session_secret)]


def _csrf_rejected() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=GENERIC_CSRF_REJECTED_MESSAGE,
        headers=authentication_security_headers(),
    )


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=GENERIC_UNAUTHENTICATED_MESSAGE,
        headers=authentication_security_headers(),
    )


async def get_normalized_network_source(request: Request) -> NormalizedNetworkSource:
    """Normalize only the direct request peer; forwarding headers are intentionally ignored."""
    return normalize_direct_peer(request)


def normalize_direct_peer(request: Request) -> NormalizedNetworkSource:
    peer = request.client
    if peer is None:
        return NormalizedNetworkSource(UNKNOWN_NETWORK_SOURCE)
    try:
        parsed = ip_address(peer.host)
    except ValueError:
        return NormalizedNetworkSource(UNKNOWN_NETWORK_SOURCE)
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return NormalizedNetworkSource(_normalized_address(parsed))


def _normalized_address(address: IPv4Address | IPv6Address) -> str:
    return address.compressed


NetworkSourceDependency = Annotated[NormalizedNetworkSource, Depends(get_normalized_network_source)]


def authentication_security_headers() -> dict[str, str]:
    return dict(AUTHENTICATION_SECURITY_HEADERS)


def generic_authentication_error_response(*, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"message": GENERIC_AUTHENTICATION_ERROR_MESSAGE},
        status_code=status_code,
        headers=authentication_security_headers(),
    )


def generic_magic_link_request_response() -> JSONResponse:
    return JSONResponse(
        {"message": GENERIC_MAGIC_LINK_REQUEST_RESULT.message},
        status_code=200,
        headers=authentication_security_headers(),
    )


def magic_link_confirmation_response(*, token: str, return_target: str) -> HTMLResponse:
    safe_token = escape(token, quote=True)
    safe_return_target = escape(return_target, quote=True)
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<title>Confirm sign in | MintFlow</title></head><body>"
        "<main><h1>Confirm sign in</h1>"
        "<p>Continue to sign in to MintFlow.</p>"
        f'<form method="post" action="{MAGIC_LINK_CONFIRMATION_PATH}">'
        f'<input type="hidden" name="token" value="{safe_token}">'
        f'<input type="hidden" name="return_target" value="{safe_return_target}">'
        '<button type="submit">Continue</button></form></main></body></html>',
        headers=authentication_security_headers(),
    )


def generic_magic_link_confirmation_failure_response() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<title>Sign-in link unavailable | MintFlow</title></head><body>"
        "<main><h1>Sign-in link unavailable</h1>"
        "<p>This sign-in link cannot be used. Request a new link to continue.</p>"
        "</main></body></html>",
        status_code=400,
        headers=authentication_security_headers(),
    )


def has_approved_login_origin(*, request: Request, approved_origin: str) -> bool:
    """Require one byte-for-byte first-party Origin; forwarding headers are irrelevant."""
    origins = request.headers.getlist("origin")
    return len(origins) == 1 and origins[0] == approved_origin.rstrip("/")


def successful_magic_link_response(
    *,
    session_secret: str,
    return_target: str,
    link_builder: MagicLinkBuilder,
    csrf_digester: CsrfTokenDigester,
) -> RedirectResponse:
    """Map a committed application result to a clean internal redirect and secure cookies."""
    link_builder.validate_return_target(return_target)
    response = RedirectResponse(
        url=f"/{quote(return_target, safe='-._~')}",
        status_code=303,
        headers=authentication_security_headers(),
    )
    response.set_cookie(
        key=AUTHENTICATED_SESSION_COOKIE_NAME,
        value=session_secret,
        max_age=AUTHENTICATED_SESSION_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_digester.derive(session_secret=session_secret),
        max_age=AUTHENTICATED_SESSION_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=False,
        samesite="lax",
    )
    return response


def logout_response() -> JSONResponse:
    """Map any logout outcome to the same generic result and expire the session cookie.

    The outcome is uniform regardless of whether a session was found and
    revoked, was already revoked or expired, or never existed, so the
    response never discloses which case occurred.
    """
    response = JSONResponse(
        {"message": GENERIC_LOGOUT_RESULT_MESSAGE},
        status_code=200,
        headers=authentication_security_headers(),
    )
    response.delete_cookie(
        key=AUTHENTICATED_SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


async def parse_magic_link_request(request: Request) -> MagicLinkRequestDTO | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_MAGIC_LINK_REQUEST_BODY_BYTES:
            return None
        body.extend(chunk)
    try:
        return MagicLinkRequestDTO.model_validate_json(body)
    except ValidationError:
        return None


async def parse_magic_link_confirmation(request: Request) -> MagicLinkConfirmationDTO | None:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/x-www-form-urlencoded"
    ):
        return None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_MAGIC_LINK_CONFIRMATION_BODY_BYTES:
            return None
        body.extend(chunk)
    try:
        fields = parse_qs(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        return None
    if not set(fields).issubset({"token", "return_target"}):
        return None
    tokens = fields.get("token", [])
    return_targets = fields.get("return_target", [])
    if len(tokens) != 1 or len(return_targets) > 1:
        return None
    token = tokens[0]
    if MAGIC_LINK_TOKEN_PATTERN.fullmatch(token) is None:
        return None
    return MagicLinkConfirmationDTO(token=token)


@router.api_route("/magic-link", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_magic_link(request: Request) -> HTMLResponse:
    token = request.query_params.get("token")
    return_target = request.query_params.get("return_target")
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    if token is None or MAGIC_LINK_TOKEN_PATTERN.fullmatch(token) is None or return_target is None:
        return generic_magic_link_confirmation_failure_response()
    try:
        runtime.link_builder.validate_return_target(return_target)
    except InvalidReturnTargetError:
        return generic_magic_link_confirmation_failure_response()
    return magic_link_confirmation_response(token=token, return_target=return_target)


@router.post("/magic-link", response_class=HTMLResponse)
async def consume_magic_link(
    request: Request,
    consume: ConsumeMagicLinkDependency,
) -> Response:
    runtime: AuthenticationRuntime = request.app.state.authentication_runtime
    if not has_approved_login_origin(
        request=request,
        approved_origin=runtime.link_builder.web_origin,
    ):
        return generic_magic_link_confirmation_failure_response()
    submitted_confirmation = await parse_magic_link_confirmation(request)
    if submitted_confirmation is None:
        return generic_magic_link_confirmation_failure_response()
    result = consume.execute(token=submitted_confirmation.token)
    if not result.authenticated or result.session_secret is None or result.return_target is None:
        return generic_magic_link_confirmation_failure_response()
    try:
        return successful_magic_link_response(
            session_secret=result.session_secret,
            return_target=result.return_target,
            link_builder=runtime.link_builder,
            csrf_digester=runtime.csrf_digester,
        )
    except InvalidReturnTargetError:
        return generic_magic_link_confirmation_failure_response()


@router.post("/magic-link/request", response_class=JSONResponse)
async def request_magic_link(
    request: Request,
    use_cases: AuthenticationUseCasesDependency,
    network_source: NetworkSourceDependency,
) -> JSONResponse:
    submitted_request = await parse_magic_link_request(request)
    if submitted_request is None:
        return generic_magic_link_request_response()
    try:
        use_cases.request_magic_link.execute(
            submitted_email=submitted_request.email,
            normalized_network_source=network_source.value,
            return_target=submitted_request.return_target,
        )
    except InvalidReturnTargetError:
        pass
    return generic_magic_link_request_response()


@router.post("/logout", response_class=JSONResponse)
async def logout(
    session_secret: CsrfVerifiedSessionSecretDependency,
    logout_use_case: LogoutWebSessionDependency,
) -> JSONResponse:
    logout_use_case.execute(secret=session_secret)
    return logout_response()
