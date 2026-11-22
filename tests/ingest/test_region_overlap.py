"""Property tests for the bounding-box overlap helper (hypothesis)."""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from darkfleet.ingest.regions import overlap_area, polygon_from_bbox, region_bbox

LON = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False)
LAT = st.floats(min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False)


@st.composite
def boxes(draw):
    """A box ``(lon_min, lon_max, lat_min, lat_max)`` with strict ordering."""
    lon_min, lon_max = sorted((draw(LON), draw(LON)))
    lat_min, lat_max = sorted((draw(LAT), draw(LAT)))
    assume(lon_min < lon_max and lat_min < lat_max)
    return lon_min, lon_max, lat_min, lat_max


def _region(box):
    return polygon_from_bbox(*box)


@given(boxes(), boxes())
def test_overlap_is_never_negative(a, b):
    assert overlap_area(_region(a), _region(b)) >= 0


@given(boxes(), boxes())
def test_overlap_is_symmetric(a, b):
    assert overlap_area(_region(a), _region(b)) == pytest.approx(overlap_area(_region(b), _region(a)))


@given(boxes())
def test_overlap_with_itself_is_its_area(box):
    area = (box[1] - box[0]) * (box[3] - box[2])
    assert overlap_area(_region(box), _region(box)) == pytest.approx(area)


@given(boxes(), boxes())
def test_disjoint_longitudes_do_not_overlap(a, b):
    assume(a[1] <= b[0] or b[1] <= a[0])
    assert overlap_area(_region(a), _region(b)) == 0.0


@given(boxes(), boxes())
def test_disjoint_latitudes_do_not_overlap(a, b):
    assume(a[3] <= b[2] or b[3] <= a[2])
    assert overlap_area(_region(a), _region(b)) == 0.0


@given(boxes())
def test_region_bbox_round_trips(box):
    assert region_bbox(_region(box)) == box
