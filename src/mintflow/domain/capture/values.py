import re
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Final

# A sanity bound only -- not the "today or the next local day" confirmation
# rule, which depends on the User's timezone and clock and belongs to the
# confirmation use case (CAPTURE-08), not this value object.
_MIN_YEAR: Final[int] = 2000
_MAX_YEAR: Final[int] = 2100

_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TransactionDate:
    """The user-confirmed local calendar date an expense occurred on.

    A named wrapper around a plain calendar date, kept distinct in type from
    any datetime/instant used elsewhere (receipt upload time, Expense
    creation time, recognition time, audit timestamps).
    """

    value: date

    def __post_init__(self) -> None:
        if not (_MIN_YEAR <= self.value.year <= _MAX_YEAR):
            raise ValueError(f"transaction date year must be between {_MIN_YEAR} and {_MAX_YEAR}")


@dataclass(frozen=True, slots=True)
class MerchantName:
    """A normalized, non-empty, user-visible merchant name.

    Whitespace is collapsed and trimmed; no merchant-directory matching or
    country-specific normalization is performed. Always optional at the
    entity level: use ``MerchantName | None``.
    """

    MAX_LENGTH: ClassVar[int] = 140

    value: str

    def __post_init__(self) -> None:
        normalized = _WHITESPACE_PATTERN.sub(" ", self.value.strip())
        if not normalized:
            raise ValueError("merchant name must not be empty")
        if len(normalized) > self.MAX_LENGTH:
            raise ValueError(f"merchant name must be at most {self.MAX_LENGTH} characters")
        object.__setattr__(self, "value", normalized)
