import pytest

from mintflow.domain.capture import MAX_MAJOR_UNITS, CurrencyCode, Money


def _money(minor_units: int, code: str) -> Money:
    return Money(minor_units=minor_units, currency=CurrencyCode(code))


@pytest.mark.parametrize(
    ("code", "exponent"),
    [
        ("JPY", 0),
        ("USD", 2),
        ("BHD", 3),
    ],
)
def test_accepts_amounts_at_zero_and_at_the_cap(code: str, exponent: int) -> None:
    cap = MAX_MAJOR_UNITS * (10**exponent)

    assert _money(0, code).minor_units == 0
    assert _money(cap, code).minor_units == cap


@pytest.mark.parametrize(
    ("code", "exponent"),
    [
        ("JPY", 0),
        ("USD", 2),
        ("BHD", 3),
    ],
)
def test_rejects_negative_and_over_cap_amounts(code: str, exponent: int) -> None:
    cap = MAX_MAJOR_UNITS * (10**exponent)

    with pytest.raises(ValueError, match="must not be negative"):
        _money(-1, code)
    with pytest.raises(ValueError, match="exceeds the representable maximum"):
        _money(cap + 1, code)


def test_same_currency_addition_and_comparison() -> None:
    five = _money(500, "USD")
    ten = _money(1000, "USD")

    assert five + five == ten
    assert five < ten
    assert five <= ten
    assert ten > five
    assert ten >= five
    assert five <= five
    assert five >= five


def test_cross_currency_addition_raises() -> None:
    with pytest.raises(ValueError, match="different currencies"):
        _money(100, "USD") + _money(100, "EUR")


@pytest.mark.parametrize(
    "operator",
    [
        lambda a, b: a < b,
        lambda a, b: a <= b,
        lambda a, b: a > b,
        lambda a, b: a >= b,
    ],
)
def test_cross_currency_comparison_raises(operator: object) -> None:
    usd = _money(100, "USD")
    eur = _money(100, "EUR")

    with pytest.raises(ValueError, match="different currencies"):
        operator(usd, eur)  # type: ignore[operator]


def test_currency_code_normalizes_case() -> None:
    assert CurrencyCode("usd").value == "USD"
    assert CurrencyCode(" Usd ").value == "USD"


def test_currency_code_rejects_unknown_codes() -> None:
    with pytest.raises(ValueError, match="unsupported currency code"):
        CurrencyCode("XXX")


def test_currency_catalogue_exposes_minor_unit_exponent() -> None:
    assert CurrencyCode("JPY").minor_unit_exponent == 0
    assert CurrencyCode("USD").minor_unit_exponent == 2
    assert CurrencyCode("BHD").minor_unit_exponent == 3
