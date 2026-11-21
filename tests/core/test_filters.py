"""Tests for the pure case filters."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.filters import Gap, HasSlack, is_stationary, meeting_ok, recurring_cells
from darkfleet.core.geo import GEOD, Fix

T0 = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)


def _gap(distance_m: float) -> Gap:
    """A gap starting at (25.0, 57.0) and moving north by `distance_m`."""
    lon, lat, _ = GEOD.fwd(57.0, 25.0, 0.0, distance_m)
    return Fix(25.0, 57.0, T0), Fix(float(lat), float(lon), T0), 15.0


def test_is_stationary_just_under_threshold():
    assert is_stationary(_gap(4900.0)) is True


def test_is_stationary_just_over_threshold():
    assert is_stationary(_gap(5100.0)) is False


def test_is_stationary_same_point():
    assert is_stationary(_gap(0.0)) is True


@settings(max_examples=50, deadline=None)
@given(
    lat=st.floats(-60, 60),
    lon=st.floats(-170, 170),
    km=st.floats(0.1, 50),
)
def test_same_point_is_stationary_for_any_threshold(lat: float, lon: float, km: float):
    gap: Gap = (Fix(lat, lon, T0), Fix(lat, lon, T0), 15.0)
    assert is_stationary(gap, km=km) is True


def test_recurring_cell_on_three_distinct_days():
    cell = (24.75, 57.0)
    cells = [
        (cell[0], cell[1], date(2026, 10, 1)),
        (cell[0], cell[1], date(2026, 10, 2)),
        (cell[0], cell[1], date(2026, 10, 3)),
    ]
    assert recurring_cells(cells) == {cell}


def test_recurring_cell_on_one_day_repeated_is_excluded():
    cell = (24.75, 57.0)
    cells = [(cell[0], cell[1], date(2026, 10, 1)) for _ in range(3)]
    assert recurring_cells(cells) == set()


def test_two_day_cell_needs_three_by_default():
    cell = (24.75, 57.0)
    cells = [
        (cell[0], cell[1], date(2026, 10, 1)),
        (cell[0], cell[1], date(2026, 10, 2)),
    ]
    assert recurring_cells(cells) == set()


def test_two_day_cell_appears_with_min_days_two():
    cell = (24.75, 57.0)
    cells = [
        (cell[0], cell[1], date(2026, 10, 1)),
        (cell[0], cell[1], date(2026, 10, 2)),
    ]
    assert recurring_cells(cells, min_days=2) == {cell}


def test_recurring_cells_empty_input():
    assert recurring_cells([]) == set()


@dataclass
class _Window:
    slack: float


def test_meeting_ok_at_threshold_is_false():
    assert meeting_ok(_Window(0.3)) is False


def test_meeting_ok_just_above_threshold():
    assert meeting_ok(_Window(0.31)) is True


def test_meeting_ok_zero_slack_is_false():
    assert meeting_ok(_Window(0.0)) is False


def test_meeting_ok_accepts_protocol_window():
    window: HasSlack = _Window(0.9)
    assert meeting_ok(window) is True
