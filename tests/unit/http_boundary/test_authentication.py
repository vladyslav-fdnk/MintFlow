from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine

from mintflow.application.authentication import (
    AuthenticatedWebSession,
    ConsumeMagicLink,
    CsrfTokenDigester,
)
from mintflow.application.authentication.login_challenge import MagicLinkMessage
from mintflow.application.authentication.magic_link import MagicLinkBuilder
from mintflow.config import Settings
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    AUTHENTICATED_SESSION_MAX_AGE_SECONDS,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    MAX_MAGIC_LINK_REQUEST_BODY_BYTES,
    AuthenticatedPrincipal,
    AuthenticationConfigurationError,
    AuthenticationUseCases,
    MagicLinkConfirmationDTO,
    MagicLinkRequestDTO,
    authentication_security_headers,
    build_authentication_runtime,
    generic_authentication_error_response,
    generic_magic_link_request_response,
    get_authenticated_principal,
    get_authentication_use_cases,
    get_consume_magic_link,
    get_csrf_protected_principal,
    has_approved_login_origin,
    normalize_direct_peer,
    parse_magic_link_confirmation,
    parse_magic_link_request,
    successful_magic_link_response,
)
from mintflow.infrastructure.persistence import create_session_factory


class NoOpEmailSender:
    def send_magic_link(self, message: MagicLinkMessage) -> None:
        pass


class RecordingSessionAuthentication:
    def __init__(self, result: AuthenticatedWebSession | None) -> None:
        self.result = result
        self.secrets: list[str] = []

    def execute(self, *, secret: str) -> AuthenticatedWebSession | None:
        self.secrets.append(secret)
        return self.result


def _request(
    *, client: tuple[str, int] | None, headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers or [],
        "client": client,
        "server": ("test", 80),
        "scheme": "http",
        "query_string": b"",
        "root_path": "",
        "http_version": "1.1",
    }
    return Request(scope)


@pytest.mark.anyio
async def test_extracts_only_approved_cookie_and_maps_typed_principal() -> None:
    authenticated = AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4())
    use_case = RecordingSessionAuthentication(authenticated)
    secret = "A" * 43
    request = _request(
        client=("192.0.2.10", 1234),
        headers=[
            (
                b"cookie",
                f"session={'B' * 43}; {AUTHENTICATED_SESSION_COOKIE_NAME}={secret}".encode(),
            )
        ],
    )

    principal = await get_authenticated_principal(
        request,
        use_case,  # type: ignore[arg-type]
    )

    assert principal == AuthenticatedPrincipal(
        user_id=authenticated.user_id,
        web_session_id=authenticated.session_id,
    )
    assert use_case.secrets == [secret]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cookie_header",
    [
        None,
        f"{AUTHENTICATED_SESSION_COOKIE_NAME}=",
        f"{AUTHENTICATED_SESSION_COOKIE_NAME}=too-short",
        f"session={'A' * 43}",
    ],
)
async def test_rejects_missing_empty_malformed_and_unapproved_cookies_generically(
    cookie_header: str | None,
) -> None:
    use_case = RecordingSessionAuthentication(None)
    headers = [] if cookie_header is None else [(b"cookie", cookie_header.encode())]

    with pytest.raises(HTTPException) as captured:
        await get_authenticated_principal(
            _request(client=("192.0.2.10", 1234), headers=headers),
            use_case,  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 401
    assert captured.value.detail == "Authentication required."
    assert captured.value.headers == {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }
    assert use_case.secrets == []


@pytest.mark.anyio
async def test_unknown_session_uses_same_generic_rejection() -> None:
    use_case = RecordingSessionAuthentication(None)
    secret = "A" * 43

    with pytest.raises(HTTPException) as captured:
        await get_authenticated_principal(
            _request(
                client=("192.0.2.10", 1234),
                headers=[(b"cookie", f"{AUTHENTICATED_SESSION_COOKIE_NAME}={secret}".encode())],
            ),
            use_case,  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 401
    assert captured.value.detail == "Authentication required."
    assert use_case.secrets == [secret]


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        (("192.0.2.10", 1234), "192.0.2.10"),
        (("2001:0db8::1", 1234), "2001:db8::1"),
        (("::ffff:192.0.2.10", 1234), "192.0.2.10"),
        (("not-an-address", 1234), "unknown"),
        (None, "unknown"),
    ],
)
def test_normalizes_direct_peer_deterministically(
    peer: tuple[str, int] | None, expected: str
) -> None:
    assert normalize_direct_peer(_request(client=peer)).value == expected


def test_ignores_untrusted_forwarding_headers() -> None:
    request = _request(
        client=("192.0.2.10", 1234),
        headers=[
            (b"forwarded", b"for=203.0.113.9"),
            (b"x-forwarded-for", b"198.51.100.7"),
        ],
    )

    assert normalize_direct_peer(request).value == "192.0.2.10"


@pytest.mark.anyio
async def test_composes_existing_authentication_use_cases(settings: Settings) -> None:
    engine = create_engine("sqlite://")
    runtime = build_authentication_runtime(
        settings=settings,
        session_factory=create_session_factory(engine),
        email_sender=NoOpEmailSender(),
    )
    request = _request(client=("192.0.2.10", 1234))
    request.scope["app"] = type("App", (), {"state": type("State", (), {})()})()
    request.app.state.authentication_runtime = runtime
    session = runtime.session_factory()
    try:
        use_cases = await get_authentication_use_cases(request, session)
    finally:
        session.close()
        engine.dispose()

    assert isinstance(use_cases, AuthenticationUseCases)


def test_configuration_failure_does_not_disclose_secrets(settings: Settings) -> None:
    database_secret = settings.database_url.get_secret_value()
    rate_limit_secret = settings.authentication_rate_limit_key.get_secret_value()
    invalid_settings = settings.model_copy(
        update={"authentication_web_origin": f"https://user:{rate_limit_secret}@example.test"}
    )
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(AuthenticationConfigurationError) as captured:
            build_authentication_runtime(
                settings=invalid_settings,
                session_factory=create_session_factory(engine),
                email_sender=NoOpEmailSender(),
            )
    finally:
        engine.dispose()

    rendered = str(captured.value)
    assert database_secret not in rendered
    assert rate_limit_secret not in rendered


@pytest.mark.anyio
async def test_issuance_use_case_resolution_fails_closed_without_email_adapter(
    settings: Settings,
) -> None:
    engine = create_engine("sqlite://")
    runtime = build_authentication_runtime(
        settings=settings,
        session_factory=create_session_factory(engine),
        email_sender=None,
    )
    request = _request(client=("192.0.2.10", 1234))
    request.scope["app"] = type("App", (), {"state": type("State", (), {})()})()
    request.app.state.authentication_runtime = runtime
    session = runtime.session_factory()
    try:
        with pytest.raises(AuthenticationConfigurationError):
            await get_authentication_use_cases(request, session)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.anyio
async def test_consumption_use_case_resolves_without_email_adapter(settings: Settings) -> None:
    engine = create_engine("sqlite://")
    runtime = build_authentication_runtime(
        settings=settings,
        session_factory=create_session_factory(engine),
        email_sender=None,
    )
    request = _request(client=("192.0.2.10", 1234))
    request.scope["app"] = type("App", (), {"state": type("State", (), {})()})()
    request.app.state.authentication_runtime = runtime
    session = runtime.session_factory()
    try:
        consumption = await get_consume_magic_link(request, session)
    finally:
        session.close()
        engine.dispose()

    assert isinstance(consumption, ConsumeMagicLink)


def test_security_headers_and_generic_error_are_stable() -> None:
    response = generic_authentication_error_response()

    assert authentication_security_headers() == {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_maps_bounded_magic_link_request_body() -> None:
    request = _request(client=("192.0.2.10", 1234))
    request.scope["method"] = "POST"

    async def receive() -> dict[str, object]:
        return {
            "type": "http.request",
            "body": b'{"email":"person@example.com","return_target":"dashboard"}',
            "more_body": False,
        }

    request = Request(request.scope, receive)

    assert await parse_magic_link_request(request) == MagicLinkRequestDTO(
        email="person@example.com", return_target="dashboard"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"{}",
        b'{"email":"person@example.com","return_target":"dashboard","extra":true}',
        b'{"email":"' + (b"a" * 321) + b'","return_target":"dashboard"}',
        b'{"email":"person@example.com","return_target":"' + (b"a" * 65) + b'"}',
        b"x" * (MAX_MAGIC_LINK_REQUEST_BODY_BYTES + 1),
    ],
)
async def test_rejects_malformed_or_unbounded_magic_link_request_body(body: bytes) -> None:
    request = _request(client=("192.0.2.10", 1234))

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(request.scope, receive)

    assert await parse_magic_link_request(request) is None


def test_generic_magic_link_request_result_is_stable() -> None:
    response = generic_magic_link_request_response()

    assert response.status_code == 200
    assert response.body == (
        b'{"message":"If the address can receive email, a sign-in link will arrive shortly."}'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://other.test",
        "https://app.mintflow.test/",
        "HTTPS://app.mintflow.test",
        "not-an-origin",
    ],
)
def test_login_origin_validation_fails_closed(origin: str | None) -> None:
    headers = [] if origin is None else [(b"origin", origin.encode())]
    request = _request(client=("192.0.2.10", 1234), headers=headers)

    assert not has_approved_login_origin(
        request=request, approved_origin="https://app.mintflow.test"
    )


def test_login_origin_accepts_only_configured_first_party_origin() -> None:
    request = _request(
        client=("192.0.2.10", 1234),
        headers=[
            (b"origin", b"https://app.mintflow.test"),
            (b"forwarded", b"host=attacker.test"),
            (b"x-forwarded-host", b"attacker.test"),
        ],
    )

    assert has_approved_login_origin(request=request, approved_origin="https://app.mintflow.test")


@pytest.mark.anyio
async def test_parses_bounded_first_party_magic_link_form() -> None:
    request = _request(
        client=("192.0.2.10", 1234),
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
    )
    request.scope["method"] = "POST"

    async def receive() -> dict[str, object]:
        return {
            "type": "http.request",
            "body": b"token=" + (b"A" * 43) + b"&return_target=https%3A%2F%2Fattacker.test",
            "more_body": False,
        }

    request = Request(request.scope, receive)

    assert await parse_magic_link_confirmation(request) == MagicLinkConfirmationDTO(token="A" * 43)


def test_success_response_builds_exact_host_only_cookie_and_internal_redirect() -> None:
    csrf_digester = CsrfTokenDigester(b"csrf-signing-key")
    response = successful_magic_link_response(
        session_secret="fresh-session-secret",
        return_target="dashboard",
        link_builder=MagicLinkBuilder(
            web_origin="https://app.mintflow.test",
            allowed_return_targets=frozenset({"dashboard"}),
        ),
        csrf_digester=csrf_digester,
    )

    cookies = response.headers.getlist("set-cookie")
    cookie = next(c for c in cookies if c.startswith(f"{AUTHENTICATED_SESSION_COOKIE_NAME}="))
    csrf_cookie = next(c for c in cookies if c.startswith(f"{CSRF_COOKIE_NAME}="))
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert cookie.startswith(f"{AUTHENTICATED_SESSION_COOKIE_NAME}=fresh-session-secret;")
    assert f"Max-Age={AUTHENTICATED_SESSION_MAX_AGE_SECONDS}" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie
    expected_csrf_token = csrf_digester.derive(session_secret="fresh-session-secret")
    assert csrf_cookie.startswith(f"{CSRF_COOKIE_NAME}={expected_csrf_token};")
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" in csrf_cookie
    assert "SameSite=lax" in csrf_cookie
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def _csrf_protected_request(
    *,
    runtime: object,
    session_secret: str | None,
    csrf_header: str | None,
    origin: str | None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    cookie_parts = []
    if session_secret is not None:
        cookie_parts.append(f"{AUTHENTICATED_SESSION_COOKIE_NAME}={session_secret}")
    if cookie_parts:
        headers.append((b"cookie", "; ".join(cookie_parts).encode()))
    if csrf_header is not None:
        headers.append((CSRF_HEADER_NAME.lower().encode(), csrf_header.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    request = _request(client=("192.0.2.10", 1234), headers=headers)
    request.scope["app"] = type("App", (), {"state": type("State", (), {})()})()
    request.app.state.authentication_runtime = runtime
    return request


@pytest.mark.anyio
async def test_csrf_dependency_accepts_matching_token_and_approved_origin(
    settings: Settings,
) -> None:
    engine = create_engine("sqlite://")
    runtime = build_authentication_runtime(
        settings=settings,
        session_factory=create_session_factory(engine),
        email_sender=NoOpEmailSender(),
    )
    session_secret = "A" * 43
    token = runtime.csrf_digester.derive(session_secret=session_secret)
    request = _csrf_protected_request(
        runtime=runtime,
        session_secret=session_secret,
        csrf_header=token,
        origin=settings.authentication_web_origin,
    )
    principal = AuthenticatedPrincipal(user_id=uuid4(), web_session_id=uuid4())

    result = await get_csrf_protected_principal(request, principal)

    assert result is principal
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("csrf_header", "origin", "description"),
    [
        (None, "https://app.mintflow.test", "missing token"),
        ("", "https://app.mintflow.test", "empty token"),
        ("not-the-right-token", "https://app.mintflow.test", "malformed/invalid token"),
        ("other-session-token", "https://app.mintflow.test", "token bound to another session"),
        ("valid-placeholder", None, "missing origin"),
        ("valid-placeholder", "not-a-valid-origin", "malformed origin"),
        ("valid-placeholder", "https://attacker.test", "unapproved origin"),
    ],
)
async def test_csrf_dependency_rejects_every_invalid_combination_uniformly(
    settings: Settings, csrf_header: str | None, origin: str | None, description: str
) -> None:
    engine = create_engine("sqlite://")
    runtime = build_authentication_runtime(
        settings=settings,
        session_factory=create_session_factory(engine),
        email_sender=NoOpEmailSender(),
    )
    session_secret = "A" * 43
    if csrf_header == "other-session-token":
        csrf_header = runtime.csrf_digester.derive(session_secret="B" * 43)
    elif csrf_header == "valid-placeholder":
        csrf_header = runtime.csrf_digester.derive(session_secret=session_secret)
    request = _csrf_protected_request(
        runtime=runtime,
        session_secret=session_secret,
        csrf_header=csrf_header,
        origin=origin,
    )
    principal = AuthenticatedPrincipal(user_id=uuid4(), web_session_id=uuid4())

    with pytest.raises(HTTPException) as captured:
        await get_csrf_protected_principal(request, principal)

    assert captured.value.status_code == 403, description
    assert captured.value.detail == "The request could not be verified."
    assert captured.value.headers == {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }
    engine.dispose()
