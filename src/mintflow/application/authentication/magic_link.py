from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit


class InvalidMagicLinkConfigurationError(ValueError):
    pass


class InvalidReturnTargetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MagicLinkBuilder:
    web_origin: str
    allowed_return_targets: frozenset[str]
    confirmation_path: str = "/auth/magic-link"

    def __post_init__(self) -> None:
        origin = urlsplit(self.web_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.netloc
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
        ):
            raise InvalidMagicLinkConfigurationError("web_origin must be an HTTP(S) origin")
        if not self.confirmation_path.startswith("/") or self.confirmation_path.startswith("//"):
            raise InvalidMagicLinkConfigurationError("confirmation_path must be an absolute path")

    def build(self, *, token: str, return_target: str) -> str:
        self.validate_return_target(return_target)
        origin = urlsplit(self.web_origin)
        return urlunsplit(
            (
                origin.scheme,
                origin.netloc,
                self.confirmation_path,
                urlencode({"token": token, "return_target": return_target}),
                "",
            )
        )

    def validate_return_target(self, return_target: str) -> None:
        if return_target not in self.allowed_return_targets:
            raise InvalidReturnTargetError("return_target is not approved")
