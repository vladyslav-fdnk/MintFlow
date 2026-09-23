from dataclasses import dataclass
from typing import Final

# Large representational cap (decision 15): a very high safety limit rather
# than no cap at all, expressed in major units and interpreted through each
# currency's own minor-unit exponent. Kept as a plain constant, not a narrow
# database numeric type, so it can change without a migration.
MAX_MAJOR_UNITS: Final[int] = 999_999_999


@dataclass(frozen=True, slots=True)
class _CurrencyMetadata:
    code: str
    minor_unit_exponent: int


# ISO 4217 active fiat currencies only (decision 14) -- no crypto. This is a
# deliberately small starting catalogue covering 0-, 2-, and 3-decimal
# currencies; extending it is a data change, not a domain-model change.
_CURRENCY_CATALOGUE: Final[dict[str, _CurrencyMetadata]] = {
    metadata.code: metadata
    for metadata in (
        _CurrencyMetadata(code="USD", minor_unit_exponent=2),
        _CurrencyMetadata(code="EUR", minor_unit_exponent=2),
        _CurrencyMetadata(code="GBP", minor_unit_exponent=2),
        _CurrencyMetadata(code="PLN", minor_unit_exponent=2),
        _CurrencyMetadata(code="UAH", minor_unit_exponent=2),
        _CurrencyMetadata(code="JPY", minor_unit_exponent=0),
        _CurrencyMetadata(code="BHD", minor_unit_exponent=3),
    )
}


def supported_currency_codes() -> tuple[str, ...]:
    """Every currency MintFlow accepts, in catalogue order."""
    return tuple(_CURRENCY_CATALOGUE)


@dataclass(frozen=True, slots=True)
class CurrencyCode:
    """A validated ISO 4217 alphabetic currency code.

    Normalizes to uppercase and validates membership in the supported
    currency catalogue, which also defines each currency's minor-unit
    exponent (0, 2, or 3 decimal places).
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if normalized not in _CURRENCY_CATALOGUE:
            raise ValueError(f"unsupported currency code: {self.value!r}")
        object.__setattr__(self, "value", normalized)

    @property
    def minor_unit_exponent(self) -> int:
        return _CURRENCY_CATALOGUE[self.value].minor_unit_exponent


def _max_minor_units(currency: CurrencyCode) -> int:
    scale: int = 10**currency.minor_unit_exponent
    return MAX_MAJOR_UNITS * scale


@dataclass(frozen=True, slots=True)
class Money:
    """An integer minor-unit amount in one currency.

    Never negative (decision 1: refunds are not supported in the MVP).
    Zero is representable here; only Expense confirmation rejects zero
    (decision 2), so Money stays reusable for that distinction.
    """

    minor_units: int
    currency: CurrencyCode

    def __post_init__(self) -> None:
        if self.minor_units < 0:
            raise ValueError("Money amount must not be negative")
        if self.minor_units > _max_minor_units(self.currency):
            raise ValueError("Money amount exceeds the representable maximum")

    def _require_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError("cannot combine Money values in different currencies")

    def __add__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(minor_units=self.minor_units + other.minor_units, currency=self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.minor_units >= other.minor_units
