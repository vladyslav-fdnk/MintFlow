import pytest

from mintflow.web.charts import MIN_VISIBLE, VIEW_SIZE, bar_lengths, columns


def test_no_values_draw_nothing() -> None:
    assert columns([]) == ()
    assert bar_lengths([]) == ()


def test_the_largest_value_fills_the_height_and_zero_draws_nothing() -> None:
    drawn = columns([0, 50, 200])

    assert [column.height for column in drawn] == [0.0, 25.0, VIEW_SIZE]
    assert all(column.y + column.height == VIEW_SIZE for column in drawn)


def test_a_tiny_value_stays_visible() -> None:
    assert columns([1, 1_000_000])[0].height == MIN_VISIBLE
    assert bar_lengths([1, 1_000_000]) == (MIN_VISIBLE, VIEW_SIZE)


def test_all_zero_values_draw_flat() -> None:
    assert {column.height for column in columns([0, 0, 0])} == {0.0}


@pytest.mark.parametrize("count", [1, 2, 7, 31])
def test_columns_fit_the_view_without_overlapping(count: int) -> None:
    drawn = columns([10] * count)

    assert drawn[0].x > 0 and drawn[-1].x + drawn[-1].width < VIEW_SIZE
    for left, right in zip(drawn, drawn[1:], strict=False):
        assert left.x + left.width < right.x
