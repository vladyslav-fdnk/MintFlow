import re
from dataclasses import dataclass
from typing import Final
from zoneinfo import available_timezones

_AVAILABLE_TIMEZONES: Final[frozenset[str]] = frozenset(available_timezones())


@dataclass(frozen=True, slots=True)
class Timezone:
    """A validated IANA timezone identifier.

    Determines presentation and default local dates but never alters an
    already-confirmed TransactionDate.
    """

    value: str

    def __post_init__(self) -> None:
        if self.value not in _AVAILABLE_TIMEZONES:
            raise ValueError(f"unknown IANA timezone: {self.value!r}")


_LOCALE_PATTERN = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*")


@dataclass(frozen=True, slots=True)
class Locale:
    """A validated BCP-47-compatible language tag.

    A User preference only. Never used to derive currency or country.
    """

    value: str

    def __post_init__(self) -> None:
        if _LOCALE_PATTERN.fullmatch(self.value) is None:
            raise ValueError(f"invalid BCP-47 locale tag: {self.value!r}")
        subtags = self.value.split("-")
        normalized = [subtags[0].lower()]
        normalized.extend(
            subtag.upper() if len(subtag) == 2 else subtag.lower() for subtag in subtags[1:]
        )
        object.__setattr__(self, "value", "-".join(normalized))


_UI_LANGUAGE_PATTERN = re.compile(r"[A-Za-z]{2}")


@dataclass(frozen=True, slots=True)
class UILanguage:
    """A minimal UI language selector: ISO 639-1 shape only, no translation catalogue."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if _UI_LANGUAGE_PATTERN.fullmatch(normalized) is None:
            raise ValueError(f"invalid UI language code: {self.value!r}")
        object.__setattr__(self, "value", normalized)
