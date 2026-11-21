"""Property tests for the space-time prism."""

from datetime import UTC, datetime, timedelta

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from darkfleet.core.geo import GEOD, KNOT_KM_H, Fix, distance_km
from darkfleet.core.prism import (
    day_lens_polygon,
    footprint_polygon,
    in_footprint,
    in_lens,
    is_consistent,
    lens_polygon,
)
from darkfleet.core.speeds import max_speed_knots

T0 = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
SET = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@st.composite
def gaps(draw, consistent=True):
    lat = draw(st.floats(-60, 60))
    lon = draw(st.floats(-170, 170))
    hours = draw(st.floats(2, 48))
    v = draw(st.floats(5, 25))
    reach = v * KNOT_KM_H * hours
    frac = draw(st.floats(0.0, 0.9)) if consistent else draw(st.floats(1.05, 1.5))
    dist = min(reach * frac, 3000.0)
    if not consistent:
        dist = reach * frac
    az = draw(st.floats(0, 360))
    lon2, lat2, _ = GEOD.fwd(lon, lat, az, dist * 1000)
    if abs(lat2) > 80:
        lat2 = lat
    a = Fix(lat, lon, T0)
    b = Fix(float(lat2), float(lon2), T0 + timedelta(hours=hours))
    return a, b, v


def _scatter(a, b, v, rng, n=200):
    """Random points around the gap, within reach of a."""
    reach = v * KNOT_KM_H * (b.t - a.t).total_seconds() / 3600
    az = rng.uniform(0, 360, n)
    r = rng.uniform(0, reach, n) * 1000
    lons, lats, _ = GEOD.fwd(np.full(n, a.lon), np.full(n, a.lat), az, r)
    return lats, lons


@SET
@given(gaps())
def test_endpoints_in_lens(g):
    a, b, v = g
    assert is_consistent(a, b, v)
    assert in_lens(a, b, v, a.lat, a.lon, a.t)
    assert in_lens(a, b, v, b.lat, b.lon, b.t)


@SET
@given(gaps(consistent=False))
def test_inconsistent_gives_empty(g):
    a, b, v = g
    assert not is_consistent(a, b, v)
    mid = a.t + (b.t - a.t) / 2
    assert lens_polygon(a, b, v, mid).is_empty
    assert footprint_polygon(a, b, v).is_empty


@SET
@given(gaps(), st.integers(0, 10_000))
def test_tight_gap_lens_hugs_geodesic(g, seed):
    a, b, _ = g
    d = distance_km(a.lat, a.lon, b.lat, b.lon)
    hours = (b.t - a.t).total_seconds() / 3600
    v = d / (KNOT_KM_H * hours) * (1 + 1e-9) + 1e-9
    rng = np.random.default_rng(seed)
    lats, lons = _scatter(a, b, v, rng)
    az12, _, _ = GEOD.inv(a.lon, a.lat, b.lon, b.lat)
    for lat, lon in zip(lats, lons, strict=True):
        t = a.t + (b.t - a.t) * float(rng.uniform())
        if in_lens(a, b, v, lat, lon, t):
            # on the geodesic: d(a,p)+d(p,b) == d(a,b)
            detour = distance_km(a.lat, a.lon, lat, lon) + distance_km(lat, lon, b.lat, b.lon) - d
            assert detour < 1e-3


@SET
@given(gaps(), st.floats(1.0, 2.0), st.integers(0, 10_000))
def test_monotonic_in_speed(g, k, seed):
    a, b, v = g
    rng = np.random.default_rng(seed)
    lats, lons = _scatter(a, b, v, rng)
    for lat, lon in zip(lats, lons, strict=True):
        t = a.t + (b.t - a.t) * float(rng.uniform())
        if in_lens(a, b, v, lat, lon, t):
            assert in_lens(a, b, v * k, lat, lon, t)
        if in_footprint(a, b, v, lat, lon):
            assert in_footprint(a, b, v * k, lat, lon)


@SET
@given(gaps(), st.integers(0, 10_000))
def test_time_reversal_symmetry(g, seed):
    a, b, v = g
    rng = np.random.default_rng(seed)
    origin = T0 + timedelta(days=30)
    a2 = Fix(b.lat, b.lon, origin - b.t)  # placeholder, replaced below
    a2 = Fix(b.lat, b.lon, origin - (b.t - T0))
    b2 = Fix(a.lat, a.lon, origin - (a.t - T0))
    lats, lons = _scatter(a, b, v, rng, 40)
    for lat, lon in zip(lats, lons, strict=True):
        t = a.t + (b.t - a.t) * float(rng.uniform())
        t2 = origin - (t - T0)
        assert in_lens(a, b, v, lat, lon, t) == in_lens(a2, b2, v, lat, lon, t2)


@SET
@given(gaps(), st.floats(0.05, 0.95), st.integers(0, 10_000))
def test_polygon_agrees_with_predicate(g, frac, seed):
    from shapely.geometry import Point

    a, b, v = g
    t = a.t + (b.t - a.t) * frac
    poly = lens_polygon(a, b, v, t)
    rng = np.random.default_rng(seed)
    lats, lons = _scatter(a, b, v, rng, 200)
    for lat, lon in zip(lats, lons, strict=True):
        inside_poly = poly.contains(Point(lon, lat))
        exact = in_lens(a, b, v, lat, lon, t)
        if inside_poly != exact:
            # boundary tolerance: disagreement only within 0.5 km of the edge
            r1 = v * KNOT_KM_H * (t - a.t).total_seconds() / 3600
            r2 = v * KNOT_KM_H * (b.t - t).total_seconds() / 3600
            m = min(
                abs(distance_km(a.lat, a.lon, lat, lon) - r1),
                abs(distance_km(lat, lon, b.lat, b.lon) - r2),
            )
            assert m < 0.5


@SET
@given(gaps(), st.integers(0, 10_000))
def test_footprint_agrees_with_predicate(g, seed):
    from shapely.geometry import Point

    a, b, v = g
    poly = footprint_polygon(a, b, v)
    total = v * KNOT_KM_H * (b.t - a.t).total_seconds() / 3600
    rng = np.random.default_rng(seed)
    lats, lons = _scatter(a, b, v, rng, 100)
    for lat, lon in zip(lats, lons, strict=True):
        if poly.contains(Point(lon, lat)) != in_footprint(a, b, v, lat, lon):
            s = distance_km(a.lat, a.lon, lat, lon) + distance_km(lat, lon, b.lat, b.lon)
            assert abs(s - total) < 1.0


def test_day_lens_covers_each_instant():
    a = Fix(25.10, 56.60, T0)
    b = Fix(24.40, 57.40, datetime(2026, 10, 8, 6, 0, tzinfo=UTC))
    v = max_speed_knots("tanker")
    assert is_consistent(a, b, v)
    day = a.t.date()
    u = day_lens_polygon(a, b, v, day)
    assert not u.is_empty
    from shapely.geometry import Point

    for h in (3, 8, 15, 23):
        t = datetime(2026, 10, 7, h, tzinfo=UTC)
        p = lens_polygon(a, b, v, t)
        assert u.buffer(1e-3).contains(p.representative_point())
    assert day_lens_polygon(a, b, v, datetime(2026, 10, 9, tzinfo=UTC).date()).is_empty
    assert Point(a.lon, a.lat).distance(u) < 0.05
