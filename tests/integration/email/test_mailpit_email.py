import json
import os
import time
from typing import Any
from urllib.request import urlopen
from uuid import uuid4

import pytest
from pydantic import SecretStr

from mintflow.application.authentication.login_challenge import (
    EmailDeliveryError,
    EmailSender,
    MagicLinkMessage,
)
from mintflow.config import Settings
from mintflow.infrastructure.email import MailpitEmailSender
from mintflow.main import create_app

pytestmark = pytest.mark.mailpit_integration


def _mailpit_api_url() -> str:
    api_url = os.getenv("MINTFLOW_TEST_MAILPIT_API_URL")
    if api_url is None:
        pytest.skip("MINTFLOW_TEST_MAILPIT_API_URL is required for Mailpit integration tests")
    return api_url.rstrip("/")


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=2) as response:  # noqa: S310 - test URL is explicitly configured
        return json.load(response)  # type: ignore[no-any-return]


def test_delivers_representative_magic_link_to_mailpit() -> None:
    api_url = _mailpit_api_url()
    smtp_host = os.getenv("MINTFLOW_TEST_MAILPIT_SMTP_HOST", "localhost")
    smtp_port = int(os.getenv("MINTFLOW_TEST_MAILPIT_SMTP_PORT", "1025"))
    unique_value = uuid4().hex
    recipient = f"mailpit-{unique_value}@example.com"
    raw_token = f"integration-token-{unique_value}"
    magic_link = f"https://app.mintflow.test/auth/magic-link?token={raw_token}"
    settings = Settings(
        environment="test",
        database_url=SecretStr("postgresql://test:test@localhost:5432/test"),
        authentication_rate_limit_key=SecretStr("test-rate-limit-key"),
        authentication_csrf_signing_key=SecretStr("test-csrf-signing-key"),
        authentication_web_origin="https://app.mintflow.test",
        authentication_return_targets=frozenset({"dashboard"}),
        email_backend="mailpit",
        mailpit_smtp_host=smtp_host,
        mailpit_smtp_port=smtp_port,
        mailpit_smtp_timeout_seconds=2,
        mailpit_from_email="no-reply@mintflow.dev",
    )
    sender: EmailSender = create_app(settings).state.authentication_email_sender

    sender.send_magic_link(MagicLinkMessage(recipient, magic_link))

    matching_message_id: str | None = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        messages = _get_json(f"{api_url}/api/v1/messages").get("messages", [])
        for message in messages:
            recipients = message.get("To", [])
            if any(item.get("Address") == recipient for item in recipients):
                matching_message_id = str(message["ID"])
                break
        if matching_message_id is not None:
            break
        time.sleep(0.1)

    assert matching_message_id is not None
    captured = _get_json(f"{api_url}/api/v1/message/{matching_message_id}")
    assert magic_link in captured["Text"]


def test_mailpit_unavailability_is_an_expected_delivery_failure() -> None:
    _mailpit_api_url()
    sender = MailpitEmailSender(
        host="127.0.0.1",
        port=1,
        from_email="no-reply@mintflow.dev",
        timeout_seconds=0.1,
    )

    with pytest.raises(EmailDeliveryError, match="email delivery failed"):
        sender.send_magic_link(
            MagicLinkMessage(
                "person@example.com",
                "https://app.mintflow.test/auth/magic-link?token=integration-secret",
            )
        )
