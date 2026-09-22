import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CsrfTokenDigester:
    """Derive a session-bound CSRF token without persisting any new state.

    The token is an HMAC of the raw Web-session secret under a server-side
    signing key. It is therefore automatically invalid whenever the session
    secret it was derived from is: the session authentication check that
    must run before CSRF validation already rejects revoked, expired, and
    deactivated-user sessions, and a rotated session produces a different
    secret and therefore a different token.
    """

    secret_key: bytes

    def __post_init__(self) -> None:
        if not self.secret_key:
            raise ValueError("CSRF signing key must not be empty")

    def derive(self, *, session_secret: str) -> str:
        return hmac.new(self.secret_key, session_secret.encode("utf-8"), hashlib.sha256).hexdigest()

    def matches(self, *, session_secret: str, candidate: str) -> bool:
        expected = self.derive(session_secret=session_secret)
        return hmac.compare_digest(expected, candidate)
