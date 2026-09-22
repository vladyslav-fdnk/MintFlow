import pytest

from mintflow.application.authentication.csrf import CsrfTokenDigester


def test_rejects_empty_signing_key() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        CsrfTokenDigester(b"")


def test_derive_is_deterministic_for_the_same_session_secret() -> None:
    digester = CsrfTokenDigester(b"signing-key")

    first = digester.derive(session_secret="session-secret")
    second = digester.derive(session_secret="session-secret")

    assert first == second


def test_derive_differs_across_session_secrets() -> None:
    digester = CsrfTokenDigester(b"signing-key")

    first = digester.derive(session_secret="session-a")
    second = digester.derive(session_secret="session-b")

    assert first != second


def test_derive_differs_across_signing_keys() -> None:
    first = CsrfTokenDigester(b"signing-key-one")
    second = CsrfTokenDigester(b"signing-key-two")

    assert first.derive(session_secret="session-secret") != second.derive(
        session_secret="session-secret"
    )


def test_matches_accepts_the_correct_token_for_the_exact_session() -> None:
    digester = CsrfTokenDigester(b"signing-key")
    token = digester.derive(session_secret="session-secret")

    assert digester.matches(session_secret="session-secret", candidate=token) is True


def test_matches_rejects_a_token_bound_to_another_session() -> None:
    digester = CsrfTokenDigester(b"signing-key")
    token = digester.derive(session_secret="other-session-secret")

    assert digester.matches(session_secret="session-secret", candidate=token) is False


def test_matches_rejects_a_malformed_or_empty_candidate() -> None:
    digester = CsrfTokenDigester(b"signing-key")

    assert digester.matches(session_secret="session-secret", candidate="") is False
    assert digester.matches(session_secret="session-secret", candidate="not-a-token") is False
