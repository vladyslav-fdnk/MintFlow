"""Chart geometry for server-rendered SVG (web design W4).

Charts are drawn in a 100 by 100 view box that stretches to its container, so the page needs no
script and no inline style (the CSP forbids both). Bars are decorative: every value is also
shown as text next to the bar or in a data table.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

VIEW_SIZE: Final = 100.0
# The share of each column slot left empty between columns.
COLUMN_GAP_RATIO: Final = 0.2
# A non-zero value never draws thinner than this, so it stays visible next to zero.
MIN_VISIBLE: Final = 1.0


@dataclass(frozen=True, slots=True)
class Column:
    x: float
    y: float
    width: float
    height: float


def _scaled(value: int, maximum: int) -> float:
    if value <= 0 or maximum <= 0:
        return 0.0
    return max(round(value / maximum * VIEW_SIZE, 2), MIN_VISIBLE)


def nice_ceiling(value: int) -> int:
    """The smallest 1, 2, 2.5, or 5 times a power of ten that is at least ``value``.

    Used as the top of a value axis, so its labels are round numbers.
    """
    if value <= 0:
        return 0
    magnitude = 1
    while magnitude * 10 <= value:
        magnitude *= 10
    for step in (1, 2, 2.5, 5, 10):
        candidate = int(step * magnitude)
        if candidate >= value:
            return candidate
    return 10 * magnitude


def columns(values: Sequence[int], *, maximum: int | None = None) -> tuple[Column, ...]:
    """One column per value, left to right, bottom-aligned, scaled to ``maximum``.

    ``maximum`` defaults to the largest value; a rounded axis top can be passed instead.
    """
    if not values:
        return ()
    maximum = max(values) if maximum is None else maximum
    slot = VIEW_SIZE / len(values)
    width = round(slot * (1 - COLUMN_GAP_RATIO), 2)
    result = []
    for index, value in enumerate(values):
        height = _scaled(value, maximum)
        result.append(
            Column(
                x=round(index * slot + slot * COLUMN_GAP_RATIO / 2, 2),
                y=round(VIEW_SIZE - height, 2),
                width=width,
                height=height,
            )
        )
    return tuple(result)


def bar_lengths(values: Sequence[int]) -> tuple[float, ...]:
    """Horizontal bar lengths, the largest value spanning the full width."""
    maximum = max(values, default=0)
    return tuple(_scaled(value, maximum) for value in values)


# A circle of this radius has a circumference of 100, so dash lengths are percentages.
DONUT_RADIUS: Final = 15.9155
# Space left between neighbouring slices, in percent of the circle.
DONUT_GAP: Final = 0.6


@dataclass(frozen=True, slots=True)
class Slice:
    dasharray: str
    dashoffset: float


def donut(basis_points: Sequence[int]) -> tuple[Slice, ...]:
    """Slices for shares in basis points, clockwise from the top, drawn as circle strokes."""
    total = sum(basis_points)
    if total <= 0:
        return ()
    slices = []
    start = 0.0
    for share in basis_points:
        length = share / total * 100
        drawn = max(length - DONUT_GAP, 0.0) if len(basis_points) > 1 else length
        slices.append(
            Slice(
                dasharray=f"{drawn:.3f} {100 - drawn:.3f}",
                # A stroke starts at three o'clock; offset 25 moves it to twelve.
                dashoffset=round(25 - start, 3),
            )
        )
        start += length
    return tuple(slices)
