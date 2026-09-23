import pytest
from pydantic import SecretStr, ValidationError

from mintflow.commands import receipt_worker
from mintflow.config import Settings
from mintflow.infrastructure.recognition.azure import AzureReceiptRecognizer
from mintflow.infrastructure.recognition.fake import FakeReceiptRecognizer
from mintflow.telegram.receipt_worker import ReceiptOutcome


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr("postgresql://localhost/database"),
        "authentication_rate_limit_key": SecretStr("rate"),
        "authentication_csrf_signing_key": SecretStr("csrf"),
        "authentication_web_origin": "https://app.mintflow.test",
        "authentication_return_targets": frozenset({"dashboard"}),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_the_loop_drains_the_queue_and_idles_when_empty() -> None:
    outcomes = iter([ReceiptOutcome.READY_FOR_REVIEW, ReceiptOutcome.FAILED, None])
    rounds = iter([True, True, True, False])
    sleeps: list[float] = []

    receipt_worker.run(
        lambda: next(outcomes), should_continue=lambda: next(rounds), sleep=sleeps.append
    )

    assert sleeps == [receipt_worker.IDLE_SECONDS]


def test_a_failing_iteration_is_logged_and_the_loop_continues() -> None:
    calls: list[int] = []

    def process_next() -> ReceiptOutcome | None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database unavailable")
        return None

    rounds = iter([True, True, False])
    receipt_worker.run(process_next, should_continue=lambda: next(rounds), sleep=lambda _: None)

    assert len(calls) == 2


def test_the_fake_recognizer_is_built_only_when_configured() -> None:
    assert receipt_worker.build_recognizer(_settings()) is None
    assert isinstance(
        receipt_worker.build_recognizer(_settings(receipt_recognizer="fake")), FakeReceiptRecognizer
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_fake_recognizer_is_refused_outside_development(environment: str) -> None:
    with pytest.raises(ValidationError, match="fake receipt recognizer"):
        _settings(environment=environment, receipt_recognizer="fake")


def test_the_azure_recognizer_is_built_from_its_endpoint_and_key() -> None:
    recognizer = receipt_worker.build_recognizer(
        _settings(
            environment="production",
            receipt_recognizer="azure",
            azure_document_intelligence_endpoint="https://sample.cognitiveservices.azure.com",
            azure_document_intelligence_key=SecretStr("key"),
        )
    )

    assert isinstance(recognizer, AzureReceiptRecognizer)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({}, "endpoint and key", id="nothing"),
        pytest.param(
            {"azure_document_intelligence_endpoint": "https://sample.azure.com"},
            "endpoint and key",
            id="no key",
        ),
        pytest.param(
            {
                "azure_document_intelligence_endpoint": "http://sample.azure.com",
                "azure_document_intelligence_key": SecretStr("key"),
            },
            "https",
            id="plain http",
        ),
    ],
)
def test_the_azure_recognizer_needs_an_https_endpoint_and_a_key(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(receipt_recognizer="azure", **overrides)
