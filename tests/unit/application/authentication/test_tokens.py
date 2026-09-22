import base64

from mintflow.application.authentication.tokens import generate_token, hash_token


def test_generated_token_contains_at_least_32_random_bytes_and_is_url_safe() -> None:
    generated = generate_token()
    padding = "=" * (-len(generated.raw) % 4)

    assert len(base64.urlsafe_b64decode(generated.raw + padding)) >= 32
    assert generated.raw.isascii()
    assert "+" not in generated.raw
    assert "/" not in generated.raw


def test_hash_is_deterministic_and_fixed_length() -> None:
    assert hash_token("same-token") == hash_token("same-token")
    assert len(hash_token("same-token")) == 32


def test_different_generated_tokens_and_hashes_differ() -> None:
    first = generate_token()
    second = generate_token()

    assert first.raw != second.raw
    assert first.digest != second.digest
