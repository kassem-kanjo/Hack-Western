"""Space-time prism for an AIS gap (Sharma & Shekhar, ACM TIST 2022).

A vessel seen at `a` (time t0) and next at `b` (t1) with speed cap v can only have been at p at
time t if it could reach p from a AND still reach b: d(a,p) <= v(t-t0) and d(p,b) <= v(t1-t).
The set of such p is the "lens" (intersection of two discs) at time t; the union over t is the
"footprint" (an ellipse with foci a and b). Distances are exact geodesics.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .geo import GEOD, KNOT_KM_H, Fix, distance_km

EPS_KM = 1e-6
_MIN_R_KM = 1e-3  # avoids degenerate zero-radius circles at t0 / t1


def _secs(a: Fix, b: Fix) -> float:
    return (b.t - a.t).total_seconds()


def _reach_km(v_kn: float, seconds: float) -> float:
    return v_kn * KNOT_KM_H * seconds / 3600.0


def is_consistent(a: Fix, b: Fix, v_kn: float) -> bool:
    """True if the vessel could physically have travelled a -> b at <= v_kn."""
    s = _secs(a, b)
    if s < 0:
        return False
    return distance_km(a.lat, a.lon, b.lat, b.lon) <= _reach_km(v_kn, s) + EPS_KM


def in_lens(a: Fix, b: Fix, v_kn: float, lat: float, lon: float, t: datetime) -> bool:
    """Exact: could the vessel be at (lat, lon) at time t?"""
    if not (a.t <= t <= b.t):
        return False
    r1 = _reach_km(v_kn, (t - a.t).total_seconds())
    r2 = _reach_km(v_kn, (b.t - t).total_seconds())
    return (
        distance_km(a.lat, a.lon, lat, lon) <= r1 + EPS_KM
        and distance_km(lat, lon, b.lat, b.lon) <= r2 + EPS_KM
    )


def in_footprint(a: Fix, b: Fix, v_kn: float, lat: float, lon: float) -> bool:
    """Could the vessel have visited (lat, lon) at any time during the gap?"""
    total = _reach_km(v_kn, _secs(a, b))
    return distance_km(a.lat, a.lon, lat, lon) + distance_km(lat, lon, b.lat, b.lon) <= total + EPS_KM


def _midpoint(a: Fix, b: Fix) -> tuple[float, float]:
    az12, _, dist = GEOD.inv(a.lon, a.lat, b.lon, b.lat)
    mlon, mlat, _ = GEOD.fwd(a.lon, a.lat, az12, dist / 2.0)
    return float(mlat), float(mlon)


def _circle(lat: float, lon: float, r_km: float, n: int) -> list[tuple[float, float]]:
    """Geodesic circle as (lon, lat) vertices; exact vertices, chord edges."""
    r_m = max(r_km, _MIN_R_KM) * 1000.0
    az = np.linspace(0.0, 360.0, n, endpoint=False)
    lons, lats, _ = GEOD.fwd(np.full(n, lon), np.full(n, lat), az, np.full(n, r_m))
    return list(zip(lons, lats, strict=True))


def _aeqd(lat: float, lon: float) -> tuple[Transformer, Transformer]:
    crs = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
    return (
        Transformer.from_crs("EPSG:4326", crs, always_xy=True),
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True),
    )


def lens_polygon(a: Fix, b: Fix, v_kn: float, t: datetime, n: int = 128) -> Polygon:
    """Lens at time t as a lon/lat polygon; empty if t is outside the gap or inputs inconsistent.

    Circle vertices are exact geodesic points; the intersection is done in an azimuthal-
    equidistant plane centred on the gap midpoint so it is metric, then projected back.
    """
    if not (a.t <= t <= b.t) or not is_consistent(a, b, v_kn):
        return Polygon()
    r1 = _reach_km(v_kn, (t - a.t).total_seconds())
    r2 = _reach_km(v_kn, (b.t - t).total_seconds())
    mlat, mlon = _midpoint(a, b)
    fwd, inv = _aeqd(mlat, mlon)

    def planar(lat: float, lon: float, r: float) -> Polygon:
        pts = _circle(lat, lon, r, n)
        xs, ys = fwd.transform([p[0] for p in pts], [p[1] for p in pts])
        return Polygon(zip(xs, ys, strict=True)).buffer(0)

    inter = planar(a.lat, a.lon, r1).intersection(planar(b.lat, b.lon, r2))
    if inter.is_empty or inter.geom_type != "Polygon":
        if inter.is_empty or not hasattr(inter, "geoms"):
            return Polygon()
        inter = max(inter.geoms, key=lambda g: g.area)
    xs, ys = inv.transform(*inter.exterior.xy)
    return Polygon(zip(xs, ys, strict=True))


def day_lens_polygon(a: Fix, b: Fix, v_kn: float, day: date, step_minutes: int = 30):
    """Union of lenses over the part of `day` (UTC) inside [t0, t1]: where the vessel could be
    if all we know is that a satellite saw something at some unknown time that day."""
    d0 = datetime(day.year, day.month, day.day, tzinfo=UTC)
    start, end = max(a.t, d0), min(b.t, d0 + timedelta(days=1))
    if end < start or not is_consistent(a, b, v_kn):
        return Polygon()
    times = []
    t = start
    while t < end:
        times.append(t)
        t += timedelta(minutes=step_minutes)
    times.append(end)
    polys = [p for p in (lens_polygon(a, b, v_kn, x) for x in times) if not p.is_empty]
    return unary_union(polys) if polys else Polygon()


def footprint_polygon(a: Fix, b: Fix, v_kn: float, n: int = 128) -> Polygon:
    """Union of all lenses: points with d(a,p)+d(p,b) <= v*T. Built by bisecting the boundary
    radius along rays from the midpoint (geodesic-exact, no ellipse approximation)."""
    total_m = _reach_km(v_kn, _secs(a, b)) * 1000.0
    if not is_consistent(a, b, v_kn):
        return Polygon()
    mlat, mlon = _midpoint(a, b)
    az = np.linspace(0.0, 360.0, n, endpoint=False)
    lo = np.zeros(n)
    hi = np.full(n, total_m / 2.0 + 1.0)  # semi-major axis is total/2; ray radius never exceeds it

    def excess(r):
        lons, lats, _ = GEOD.fwd(np.full(n, mlon), np.full(n, mlat), az, r)
        _, _, d1 = GEOD.inv(lons, lats, np.full(n, a.lon), np.full(n, a.lat))
        _, _, d2 = GEOD.inv(lons, lats, np.full(n, b.lon), np.full(n, b.lat))
        return d1 + d2 - total_m

    for _ in range(45):
        mid = (lo + hi) / 2
        inside = excess(mid) <= 0
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    lons, lats, _ = GEOD.fwd(np.full(n, mlon), np.full(n, mlat), az, lo)
    return Polygon(zip(lons, lats, strict=True))
