"""Deployed email, trusted proxies, and the production start-up check (operations O2-O4)."""

from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from mintflow.config import (
    ProductionConfigurationError,
    Settings,
    production_configuration_problems,
)
from mintflow.http.authentication import normalize_direct_peer
from mintflow.infrastructure.email import SmtpEmailSender, create_email_sender
from mintflow.main import create_app

SMTP: dict[str, Any] = {
    "email_backend": "smtp",
    "smtp_host": "smtp.resend.com",
    "smtp_username": "resend",
    "smtp_password": SecretStr("re_secret"),
    "smtp_from_email": "no-reply@mintflow.example",
}
TELEGRAM: dict[str, Any] = {
    "telegram_bot_token": SecretStr("123:token"),
    "telegram_bot_username": "mintflow_bot",
    "telegram_webhook_secret": SecretStr("hook-secret"),
}


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "production",
        "log_level": "CRITICAL",
        "database_url": SecretStr("postgresql://test:test@localhost:5432/test"),
        "authentication_rate_limit_key": SecretStr("rate"),
        "authentication_csrf_signing_key": SecretStr("csrf"),
        "authentication_web_origin": "https://app.mintflow.example",
        "authentication_return_targets": frozenset({"dashboard"}),
        **SMTP,
        **TELEGRAM,
    }
    values.update(overrides)
    return Settings(**values)


# --- SMTP settings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["smtp_host", "smtp_username", "smtp_password", "smtp_from_email"]
)
def test_smtp_needs_every_connection_value(missing: str) -> None:
    with pytest.raises(ValidationError, match="SMTP email requires") as raised:
        _settings(**{missing: None})

    assert "re_secret" not in str(raised.value)


def test_the_smtp_backend_builds_the_smtp_sender() -> None:
    sender = create_email_sender(_settings(smtp_tls="starttls", smtp_port=587))

    assert isinstance(sender, SmtpEmailSender)


def test_smtp_defaults_to_implicit_tls_on_port_465() -> None:
    settings = _settings()

    assert (settings.smtp_port, settings.smtp_tls) == (465, "implicit")


def test_there_is_no_unencrypted_smtp_mode() -> None:
    with pytest.raises(ValidationError):
        _settings(smtp_tls="none")


# --- the production start-up check --------------------------------------------------------------


def test_a_complete_production_configuration_has_no_problems() -> None:
    assert production_configuration_problems(_settings()) == []


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        pytest.param({"enable_api_docs": True}, "API docs must be off", id="api docs"),
        pytest.param(
            {"authentication_web_origin": "http://app.mintflow.example"}, "https://", id="http"
        ),
        pytest.param(
            {key: None for key in SMTP} | {"email_backend": None}, "email backend", id="no email"
        ),
        pytest.param({key: None for key in TELEGRAM}, "Telegram", id="no telegram"),
    ],
)
def test_the_web_app_refuses_to_start_with_each_production_mistake(
    overrides: dict[str, Any], problem: str
) -> None:
    settings = _settings(**overrides)

    assert any(problem in item for item in production_configuration_problems(settings))
    with pytest.raises(ProductionConfigurationError, match=problem):
        create_app(settings)


def test_the_message_lists_every_problem_and_no_secret() -> None:
    settings = _settings(enable_api_docs=True, authentication_web_origin="http://x.example")

    with pytest.raises(ProductionConfigurationError) as raised:
        create_app(settings)

    assert "API docs" in str(raised.value) and "https://" in str(raised.value)
    for secret in ("re_secret", "123:token", "hook-secret", "csrf", "rate"):
        assert secret not in str(raised.value)


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
def test_other_environments_are_not_held_to_the_production_rules(environment: str) -> None:
    settings = _settings(
        environment=environment, enable_api_docs=True, **{k: None for k in TELEGRAM}
    )

    assert production_configuration_problems(settings) == []


def test_a_production_app_without_a_recognizer_still_starts() -> None:
    # Receipts then use the manual fallback until a recognizer is configured (RCPT-07).
    assert isinstance(create_app(_settings(receipt_recognizer=None)), FastAPI)


# --- the client address behind a proxy ----------------------------------------------------------


def test_trusted_proxies_must_be_addresses_or_networks() -> None:
    with pytest.raises(ValidationError, match="trusted proxies"):
        _settings(trusted_proxies=frozenset({"caddy"}))


async def _peer(settings: Settings, headers: dict[str, str]) -> str:
    application = create_app(settings)

    @application.get("/_peer")
    async def peer(request: Request) -> dict[str, str]:
        return {"source": normalize_direct_peer(request).value}

    # httpx's ASGI transport connects from 127.0.0.1.
    transport = ASGITransport(app=application, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="https://app.mintflow.example") as client:
        response = await client.get("/_peer", headers=headers)
    return str(response.json()["source"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("trusted", "forwarded", "expected"),
    [
        pytest.param(frozenset(), "203.0.113.9", "127.0.0.1", id="no trusted proxy: ignored"),
        pytest.param(frozenset({"10.0.0.0/8"}), "203.0.113.9", "127.0.0.1", id="untrusted peer"),
        pytest.param(frozenset({"127.0.0.1"}), "203.0.113.9", "203.0.113.9", id="trusted peer"),
        pytest.param(
            frozenset({"127.0.0.0/8"}), "203.0.113.9", "203.0.113.9", id="trusted network"
        ),
        # A client can send its own X-Forwarded-For; the proxy appends the real address, and only
        # the rightmost untrusted hop counts.
        pytest.param(
            frozenset({"127.0.0.1"}), "6.6.6.6, 203.0.113.9", "203.0.113.9", id="spoofed hop"
        ),
    ],
)
async def test_login_throttling_sees_the_client_behind_a_trusted_proxy_only(
    trusted: frozenset[str], forwarded: str, expected: str
) -> None:
    settings = _settings(environment="test", trusted_proxies=trusted)

    assert await _peer(settings, {"X-Forwarded-For": forwarded}) == expected
