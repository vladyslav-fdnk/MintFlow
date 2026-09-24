import pytest

from mintflow.web.charts import MIN_VISIBLE, VIEW_SIZE, bar_lengths, columns, donut, nice_ceiling


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


@pytest.mark.parametrize(
    ("value", "top"),
    [
        (0, 0),
        (1, 1),
        (3, 5),
        (11, 20),
        (16_499, 20_000),
        (20_000, 20_000),
        (21_000, 25_000),
        (99_999, 100_000),
    ],
)
def test_the_axis_top_is_a_round_number_at_least_the_value(value: int, top: int) -> None:
    assert nice_ceiling(value) == top


def test_columns_can_be_scaled_to_an_axis_top() -> None:
    assert [column.height for column in columns([50, 100], maximum=200)] == [25.0, 50.0]


def test_donut_slices_start_at_the_top_and_follow_each_other() -> None:
    first, second = donut([7_500, 2_500])

    assert first.dashoffset == 25.0
    assert second.dashoffset == -50.0
    assert first.dasharray.split()[0] == "74.400"  # 75% minus the gap between slices


def test_a_single_category_is_a_full_ring() -> None:
    [only] = donut([10_000])

    assert only.dasharray == "100.000 0.000"


def test_no_shares_draw_no_donut() -> None:
    assert donut([]) == () and donut([0]) == ()
