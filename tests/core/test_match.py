from datetime import UTC, date, datetime, timedelta

from shapely.geometry import box

from darkfleet.core.geo import GEOD, KNOT_KM_H, Fix, distance_km
from darkfleet.core.match import SarCell, candidate_sightings, rendezvous_windows, verdict_at_scene
from darkfleet.core.prism import lens_polygon
from darkfleet.core.speeds import max_speed_knots

T0 = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
V = max_speed_knots("tanker")
A = Fix(25.10, 56.60, T0)
B = Fix(24.40, 57.40, T1)


def _crossing_gap(heading, hours=10.0, v=15.0):
    """Gap passing through (25, 57) at T0+hours, straight line at v knots."""
    r = v * KNOT_KM_H * hours * 1000
    lon0, lat0, _ = GEOD.fwd(57.0, 25.0, (heading + 180) % 360, r)
    lon1, lat1, _ = GEOD.fwd(57.0, 25.0, heading, r)
    return Fix(lat0, lon0, T0), Fix(lat1, lon1, T0 + timedelta(hours=2 * hours)), v


def test_rendezvous_same_point_same_time():
    x = _crossing_gap(45.0)
    y = _crossing_gap(135.0)
    # real tracks used 15 kn; the cap is higher, so lenses have area (not a single point)
    x, y = (x[0], x[1], 20.0), (y[0], y[1], 20.0)
    ws = rendezvous_windows(x, y)
    meet = T0 + timedelta(hours=10)
    assert ws and any(w.start <= meet <= w.end for w in ws)
    assert all(w.max_overlap_km2 > 0 for w in ws)


def test_rendezvous_far_apart_empty():
    x = _crossing_gap(45.0)
    a, b, v = _crossing_gap(135.0)
    x = (x[0], x[1], 20.0)
    v = 20.0
    far = (Fix(a.lat + 20, a.lon, a.t), Fix(b.lat + 20, b.lon, b.t), v)
    assert rendezvous_windows(x, far) == []


def test_candidates_ranked_and_ais_cleared():
    mid = SarCell(24.75, 57.0, date(2026, 10, 7))
    far = SarCell(35.0, 57.0, date(2026, 10, 7))
    off_day = SarCell(24.75, 57.0, date(2026, 10, 12))
    res = candidate_sightings(A, B, V, [mid, far, off_day])
    assert [c.cell for c in res] == [mid]
    assert 0.0 <= res[0].slack <= 1.0
    ais = [(24.75, 57.0, datetime(2026, 10, 7, 5, tzinfo=UTC))]
    assert candidate_sightings(A, B, V, [mid], ais) == []
    ais_other_day = [(24.75, 57.0, datetime(2026, 10, 8, 5, tzinfo=UTC))]
    assert len(candidate_sightings(A, B, V, [mid], ais_other_day)) == 1


def test_slack_deeper_is_higher():
    centre = SarCell(24.75, 57.0, date(2026, 10, 7))
    edge = SarCell(26.5, 56.0, date(2026, 10, 7))
    res = {c.cell: c.slack for c in candidate_sightings(A, B, V, [centre, edge])}
    assert centre in res
    assert edge not in res or res[centre] > res[edge]


def _cell_box(cell):
    h = cell.size_deg / 2.0
    return box(cell.lon - h, cell.lat - h, cell.lon + h, cell.lat + h)


def test_scene_inside_lens():
    mid = SarCell(24.75, 57.0, date(2026, 10, 7))
    scene_time = T0 + timedelta(hours=12)  # middle of the gap, where the lens is largest
    inside, slack, reason = verdict_at_scene(A, B, V, mid, scene_time)
    assert inside is True
    assert 0.0 < slack <= 1.0
    assert reason.startswith("inside lens")
    assert lens_polygon(A, B, V, scene_time).intersects(_cell_box(mid))


def test_day_lens_but_not_scene_lens():
    mid = SarCell(24.75, 57.0, date(2026, 10, 7))
    # the day lens says yes (any moment of 2026-10-07 works) ...
    assert [c.cell for c in candidate_sightings(A, B, V, [mid])] == [mid]
    # ... but the instant says no: half an hour after the first fix the vessel cannot be here yet
    assert verdict_at_scene(A, B, V, mid, T0 + timedelta(minutes=30)) == (
        False,
        0.0,
        "outside lens at scene time",
    )


def test_scene_outside_gap():
    mid = SarCell(24.75, 57.0, date(2026, 10, 7))
    assert verdict_at_scene(A, B, V, mid, T1 + timedelta(hours=1)) == (
        False,
        0.0,
        "scene time outside the gap",
    )


def test_inconsistent_gap():
    mid = SarCell(24.75, 57.0, date(2026, 10, 7))
    d_ab = distance_km(A.lat, A.lon, B.lat, B.lon)
    # ~112 km in 28 h would need ~2.2 kn, so 1 kn cannot cover it
    assert d_ab > 1.0 * KNOT_KM_H * 28
    assert verdict_at_scene(A, B, 1.0, mid, T0 + timedelta(hours=12)) == (
        False,
        0.0,
        "gap not consistent at this speed",
    )


def test_slack_in_unit_range():
    for hours in (1, 12):
        scene_time = T0 + timedelta(hours=hours)
        c = lens_polygon(A, B, V, scene_time).centroid
        cell = SarCell(c.y, c.x, date(2026, 10, 7))
        inside, slack, _ = verdict_at_scene(A, B, V, cell, scene_time)
        assert inside is True
        assert 0.0 <= slack <= 1.0
    # T0+30h is past the end of the gap, so slack is 0 and still in range
    mid = SarCell(24.75, 57.0, date(2026, 10, 7))
    _, slack_outside, _ = verdict_at_scene(A, B, V, mid, T0 + timedelta(hours=30))
    assert 0.0 <= slack_outside <= 1.0
