from datetime import UTC, date, datetime, timedelta

from darkfleet.core.geo import GEOD, KNOT_KM_H, Fix
from darkfleet.core.match import SarCell, candidate_sightings, rendezvous_windows
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
