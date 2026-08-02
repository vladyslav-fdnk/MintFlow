import pytest
from fastapi import Request
from sqlalchemy import create_engine

from mintflow.application.authentication.login_challenge import MagicLinkMessage
from mintflow.config import Settings
from mintflow.http.authentication import (
    AuthenticationConfigurationError,
    AuthenticationUseCases,
    authentication_security_headers,
    build_authentication_runtime,
    generic_authentication_error_response,
    get_authentication_use_cases,
    normalize_direct_peer,
)
from mintflow.infrastructure.persistence import create_session_factory


class NoOpEmailSender:
    def send_magic_link(self, message: MagicLinkMessage) -> None:
        pass


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
async def test_use_case_resolution_fails_closed_without_email_adapter(settings: Settings) -> None:
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


def test_security_headers_and_generic_error_are_stable() -> None:
    response = generic_authentication_error_response()

    assert authentication_security_headers() == {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
