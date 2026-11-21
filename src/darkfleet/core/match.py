"""Matching SAR detections and rendezvous windows against prisms.

SAR cells carry only a DATE, so a cell is a candidate if the vessel could have been in it at any
moment of that UTC day (day lens), unless AIS shows another vessel there that day (then the SAR
blob is probably that ship, not our dark one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .geo import GEOD, Fix, distance_km, distance_km_array
from .prism import (
    _reach_km,
    day_lens_polygon,
    footprint_polygon,
    is_consistent,
    lens_polygon,
    unwrap_lon,
    unwrap_polygon,
)

Gap = tuple[Fix, Fix, float]


@dataclass(frozen=True)
class SarCell:
    lat: float
    lon: float
    day: date
    size_deg: float = 0.01


@dataclass(frozen=True)
class Candidate:
    cell: SarCell
    slack: float  # 1 = deep inside the lens, 0 = on its edge


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    max_overlap_km2: float
    centroid_lat: float
    centroid_lon: float


def _slack(a: Fix, b: Fix, v_kn: float, cell: SarCell, step_minutes: int) -> float:
    """Best margin (km) over the day's sample times, normalised by the best possible margin."""
    from datetime import UTC

    d0 = datetime(cell.day.year, cell.day.month, cell.day.day, tzinfo=UTC)
    start, end = max(a.t, d0), min(b.t, d0 + timedelta(days=1))
    total = _reach_km(v_kn, (b.t - a.t).total_seconds())
    d_ab = float(distance_km_array(a.lat, a.lon, b.lat, b.lon))
    best_possible = (total - d_ab) / 2.0
    if end < start:
        return 0.0
    da = float(distance_km_array(a.lat, a.lon, cell.lat, cell.lon))
    db = float(distance_km_array(cell.lat, cell.lon, b.lat, b.lon))
    best = -1e18
    t = start
    while True:
        m = min(
            _reach_km(v_kn, (t - a.t).total_seconds()) - da, _reach_km(v_kn, (b.t - t).total_seconds()) - db
        )
        best = max(best, m)
        if t >= end:
            break
        t = min(t + timedelta(minutes=step_minutes), end)
    if best_possible <= 1e-9:
        return 1.0 if best >= -1e-6 else 0.0
    return max(0.0, min(1.0, best / best_possible))


def candidate_sightings(
    a: Fix,
    b: Fix,
    v_kn: float,
    cells,
    ais_positions=(),
    clear_km: float = 2.0,
    step_minutes: int = 30,
) -> list[Candidate]:
    """SAR cells the dark vessel could have produced, ranked by `slack`."""
    if not is_consistent(a, b, v_kn):
        return []
    day_polys: dict[date, Polygon] = {}
    out: list[Candidate] = []
    for c in cells:
        if c.day not in day_polys:
            day_polys[c.day] = day_lens_polygon(a, b, v_kn, c.day, step_minutes)
        poly = day_polys[c.day]
        h = c.size_deg / 2.0
        if poly.is_empty:
            continue
        # Polygons are unwrapped relative to their own centre, so test the cell in the
        # same longitude frame (this is what makes a cell just across 180° match).
        clon = float(unwrap_lon(c.lon, float(poly.centroid.x)))
        if not poly.intersects(box(clon - h, c.lat - h, clon + h, c.lat + h)):
            continue
        if any(
            t.date() == c.day and float(distance_km_array(lat, lon, c.lat, c.lon)) <= clear_km
            for lat, lon, t in ais_positions
        ):
            continue
        out.append(Candidate(c, _slack(a, b, v_kn, c, step_minutes)))
    return out


def verdict_at_scene(
    a: Fix, b: Fix, v_kn: float, cell: SarCell, scene_time: datetime
) -> tuple[bool, float, str]:
    """Could the vessel have been inside `cell` at the instant `scene_time`?

    Returns (inside, slack, reason). `inside` is True if the cell square (cell.size_deg wide, centred on
    cell.lat/cell.lon) intersects lens_polygon(a, b, v_kn, scene_time). `slack` is in [0, 1]: 0 on the lens
    edge or outside, 1 deep inside; it is the cell centre's margin to the lens boundary divided by the best
    possible margin at that instant.
    """
    if not is_consistent(a, b, v_kn):
        return (False, 0.0, "gap not consistent at this speed")
    if not (a.t <= scene_time <= b.t):
        return (False, 0.0, "scene time outside the gap")
    lens = lens_polygon(a, b, v_kn, scene_time)
    if lens.is_empty:
        return (False, 0.0, "no lens at scene time")
    h = cell.size_deg / 2.0
    clon = float(unwrap_lon(cell.lon, float(lens.centroid.x)))
    square = box(clon - h, cell.lat - h, clon + h, cell.lat + h)
    if not lens.intersects(square):
        return (False, 0.0, "outside lens at scene time")
    r1 = _reach_km(v_kn, (scene_time - a.t).total_seconds())
    r2 = _reach_km(v_kn, (b.t - scene_time).total_seconds())
    da = distance_km(a.lat, a.lon, cell.lat, cell.lon)
    db = distance_km(cell.lat, cell.lon, b.lat, b.lon)
    d_ab = distance_km(a.lat, a.lon, b.lat, b.lon)
    margin = min(r1 - da, r2 - db)
    best = (r1 + r2 - d_ab) / 2.0
    if best <= 1e-9:
        slack = 1.0 if margin >= -1e-6 else 0.0
    else:
        slack = max(0.0, min(1.0, margin / best))
    if slack == 0.0:
        return (True, slack, "inside lens at scene time, on its edge")
    return (True, slack, "inside lens at scene time")


def _area_km2(poly) -> float:
    area_m2, _ = GEOD.geometry_area_perimeter(poly)
    return abs(area_m2) / 1e6


def rendezvous_windows(
    gap_x: Gap, gap_y: Gap, step_minutes: int = 15, min_overlap_km2: float = 0.0
) -> list[Window]:
    """Time windows in which both vessels' lenses overlap (a possible meeting)."""
    ax, bx, vx = gap_x
    ay, by, vy = gap_y
    t_start, t_end = max(ax.t, ay.t), min(bx.t, by.t)
    if t_end < t_start or not (is_consistent(ax, bx, vx) and is_consistent(ay, by, vy)):
        return []
    # Cheap prefilter: if the whole-gap footprints are disjoint, no instant can overlap.
    foot_x = footprint_polygon(ax, bx, vx, 64)
    foot_y = footprint_polygon(ay, by, vy, 64)
    if foot_x.is_empty or foot_y.is_empty:
        return []
    foot_y = unwrap_polygon(foot_y, float(foot_x.centroid.x))
    if not foot_x.intersects(foot_y):
        return []
    times, t = [], t_start
    while t < t_end:
        times.append(t)
        t += timedelta(minutes=step_minutes)
    times.append(t_end)

    windows: list[Window] = []
    run: list[tuple[datetime, float, object]] = []

    def flush() -> None:
        if run:
            best = max(run, key=lambda r: r[1])
            c = best[2].centroid
            windows.append(Window(run[0][0], run[-1][0], best[1], c.y, c.x))
            run.clear()

    for t in times:
        lens_x = lens_polygon(ax, bx, vx, t)
        lens_y = lens_polygon(ay, by, vy, t)
        if lens_x.is_empty or lens_y.is_empty:
            flush()
            continue
        lens_y = unwrap_polygon(lens_y, float(lens_x.centroid.x))
        inter = lens_x.intersection(lens_y)
        if inter.is_empty:
            flush()
            continue
        area = _area_km2(unary_union([inter]))
        if area >= min_overlap_km2:
            run.append((t, area, inter))
        else:
            flush()
    flush()
    return windows
