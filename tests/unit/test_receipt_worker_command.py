import pytest
from pydantic import SecretStr, ValidationError

from mintflow.commands import receipt_worker
from mintflow.config import Settings
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
