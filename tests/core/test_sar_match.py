"""AIS matching of radar detections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from darkfleet.core.sar_match import AisFix, match_to_ais, position_at

T0 = datetime(2026, 11, 21, 2, 6, tzinfo=UTC)


def fix(minutes: float, lat: float, lon: float) -> AisFix:
    return AisFix(T0 + timedelta(minutes=minutes), lat, lon)


def test_interpolates_between_fixes() -> None:
    lat, lon = position_at([fix(-10, 26.0, 56.0), fix(10, 26.0, 56.2)], T0)
    assert abs(lat - 26.0) < 1e-9 and abs(lon - 56.1) < 1e-9


def test_fix_outside_tolerance_is_unusable() -> None:
    assert position_at([fix(-40, 26.0, 56.0)], T0) is None
    assert position_at([fix(-14, 26.0, 56.0)], T0) == (26.0, 56.0)


def test_match_radius_and_unmatched() -> None:
    tracks = {111: [fix(-5, 26.0, 56.0)], 222: [fix(-5, 26.5, 56.5)]}
    out = match_to_ais([(26.005, 56.0), (26.2, 56.2)], tracks, T0)
    assert out == [111, None]


def test_one_vessel_explains_one_detection() -> None:
    tracks = {111: [fix(0, 26.0, 56.0)]}
    out = match_to_ais([(26.001, 56.0), (26.0, 56.0)], tracks, T0)
    assert out == [None, 111]  # the closer detection wins


def test_no_ais_means_no_matches() -> None:
    assert match_to_ais([(26.0, 56.0)], {}, T0) == [None]


@given(dlat=st.floats(-0.02, 0.02), dlon=st.floats(-0.02, 0.02))
def test_match_iff_within_radius(dlat: float, dlon: float) -> None:
    from darkfleet.core.geo import distance_km

    d = distance_km(26.0, 56.0, 26.0 + dlat, 56.0 + dlon)
    out = match_to_ais([(26.0 + dlat, 56.0 + dlon)], {1: [fix(0, 26.0, 56.0)]}, T0)
    if d < 1.49:
        assert out == [1]
    if d > 1.51:
        assert out == [None]
