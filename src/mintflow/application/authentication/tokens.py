import hashlib
import secrets
from dataclasses import dataclass

TOKEN_BYTES = 32
SHA256_BYTES = 32


@dataclass(frozen=True, slots=True)
class GeneratedToken:
    raw: str
    digest: bytes


def hash_token(raw_token: str) -> bytes:
    return hashlib.sha256(raw_token.encode("utf-8")).digest()


def generate_token() -> GeneratedToken:
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    return GeneratedToken(raw=raw_token, digest=hash_token(raw_token))
