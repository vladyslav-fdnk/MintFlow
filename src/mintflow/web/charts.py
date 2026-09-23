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


def columns(values: Sequence[int]) -> tuple[Column, ...]:
    """One column per value, left to right, bottom-aligned, scaled to the largest value."""
    if not values:
        return ()
    maximum = max(values)
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
