"""Find ghost points: places where several unrelated ships each teleport to and back from.

A ghost point is not merely a position that several ships report. Ports do that all day: a hundred
moored hulls share a quay, and river berths sit "inland" behind a coarse coastline. What spoofing
looks like in the physics is different: a ship is on its own track, appears at a point tens of
kilometres away for a few minutes, then reappears back on its track. Neither leg of that detour is
physically possible for a hull, and the ship did not actually go anywhere.

The detector therefore works in two steps:

* **Teleport outlier.** A ping (or a short run of consecutive pings at the same place) is a
  teleport outlier when the implied speed from the vessel's previous ping and the implied speed to
  its next ping both exceed :data:`SPEED_FACTOR` times the class speed cap, and both legs are
  longer than :data:`MIN_LEG_KM`. Moored ships have no such legs; a ship that genuinely sails into
  port arrives slowly and continuously. A track that starts or ends on a fix cannot show a leg on
  that side, so it is not an out-and-back and does not count.
* **Sharing.** Teleport-outlier pings are bucketed into coarse cells (~0.005 degrees) and adjacent
  candidates are merged. A component whose densest 500 m disk holds at least ``min_vessels``
  distinct MMSIs is one ghost point. Every report inside that disk joins the event, because once
  several independent transmitters have been fed the same false position, the other fixes there
  are evidence of the same point.

Land is kept as extra evidence only. A fix well inside the coastline is impossible, but it is not
a trigger: a river berth is inland too. ``on_land`` and ``inland_km`` are reported alongside the
jump speeds but never create an event by themselves.

Pure: tracks and an optional land geometry go in; events come out. Nothing here reads a file.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from statistics import median
from typing import Any

from shapely import contains_xy, prepare
from shapely.errors import GEOSException
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .geo import KNOT_KM_H, distance_km
from .speeds import max_speed_knots
from .tracks import Event, Ping

BUCKET_DEG = 0.005
"""Grid cell size for sharing candidates (~550 m of latitude, less of longitude at 60 N)."""

KM_PER_DEG = 111.0
"""Coarse degrees-per-km conversion, the same approximation ``core.encounters`` uses."""

MIN_LEG_KM = 5.0
"""A detour shorter than this is ordinary GPS jitter or berth shuffling, not a teleport."""

SPEED_FACTOR = 3.0
"""A teleport leg must require more than this multiple of the vessel's class speed cap."""

DEFAULT_CAP_KN = max_speed_knots("default")
"""Fallback speed cap (margin included) when the hull's class is unknown.

``ghost_points`` receives only positions, not static ship types, so every vessel is judged
against the default cap. The rule is written per vessel so a caller that later learns the class
only has to replace this one lookup.
"""


def _implied_kn(km: float, seconds: float) -> float:
    """Speed a leg of ``km`` in ``seconds`` implies, in knots."""
    return km / (seconds / 3600.0) / KNOT_KM_H


def _cell(lat: float, lon: float) -> tuple[int, int]:
    """Coarse grid cell containing a position; negative coordinates floor correctly."""
    return math.floor(lat / BUCKET_DEG), math.floor(lon / BUCKET_DEG)


def _neighbour_extent(radius_m: float, max_abs_lat: float) -> tuple[int, int]:
    """Rows and columns of cells needed to cover ``radius_m`` at the highest latitude present.

    Longitude cells shrink with the cosine of latitude while latitude cells do not, so a fixed
    3x3 block would miss pairs east-west of the meridian at 60 N. The cosine is clamped so a
    polar input cannot demand an absurd number of columns; the Baltic is nowhere near it.
    """
    cell_m = BUCKET_DEG * 111_320.0
    rows = max(1, math.ceil(radius_m / cell_m))
    cos_lat = max(0.1, math.cos(math.radians(max_abs_lat)))
    cols = max(1, math.ceil(radius_m / (cell_m * cos_lat)))
    return rows, cols


def _neighbour_cells(centre: tuple[int, int], rows: int, cols: int) -> list[tuple[int, int]]:
    """Every cell within the block around ``centre`` (including the centre cell)."""
    return [
        (centre[0] + dy, centre[1] + dx) for dy in range(-rows, rows + 1) for dx in range(-cols, cols + 1)
    ]


def _segments(track: Sequence[Ping], radius_km: float) -> list[list[Ping]]:
    """Split a time-sorted track into runs of consecutive fixes at one place.

    A spoofed excursion is not one ping but a short visit: the falsified device keeps reporting
    the same false position for a few minutes. Grouping consecutive fixes within ``radius_km``
    lets the detector measure the jump into the visit and the jump out of it, instead of comparing
    two neighbouring false fixes that are metres apart.
    """
    segments: list[list[Ping]] = []
    for ping in sorted(track, key=lambda p: p.t):
        if segments:
            last = segments[-1][-1]
            if distance_km(last.lat, last.lon, ping.lat, ping.lon) <= radius_km:
                segments[-1].append(ping)
                continue
        segments.append([ping])
    return segments


def _teleport_segments(track: Sequence[Ping], radius_km: float) -> list[list[Ping]]:
    """Runs of fixes that a vessel jumped into and back out of impossibly fast.

    The two legs are measured from the fix before the visit and to the fix after it. Both must be
    longer than :data:`MIN_LEG_KM` and imply more than :data:`SPEED_FACTOR` times the cap: one
    impossible leg can be a bad receiver or a track boundary, while an impossible leg in *and* out
    is a detour that never happened. A visit at the start or end of a track has no second leg, so
    it cannot be called out-and-back and is skipped.
    """
    segments = _segments(track, radius_km)
    flat = [ping for segment in segments for ping in segment]
    limit_kn = SPEED_FACTOR * DEFAULT_CAP_KN
    outliers: list[list[Ping]] = []
    start = 0
    for segment in segments:
        prev = flat[start - 1] if start > 0 else None
        end = start + len(segment) - 1
        nxt = flat[end + 1] if end + 1 < len(flat) else None
        start += len(segment)
        if prev is None or nxt is None:
            continue
        first, last = segment[0], segment[-1]
        in_km = distance_km(prev.lat, prev.lon, first.lat, first.lon)
        out_km = distance_km(last.lat, last.lon, nxt.lat, nxt.lon)
        if in_km <= MIN_LEG_KM or out_km <= MIN_LEG_KM:
            continue
        in_s = (first.t - prev.t).total_seconds()
        out_s = (nxt.t - last.t).total_seconds()
        if in_s <= 0 or out_s <= 0:
            continue
        if _implied_kn(in_km, in_s) <= limit_kn or _implied_kn(out_km, out_s) <= limit_kn:
            continue
        outliers.append(segment)
    return outliers


def _inland_pings(pings: Sequence[Ping], land: BaseGeometry | None, min_inland_km: float) -> set[Ping]:
    """Pings more than ``min_inland_km`` inside ``land``; empty when no usable land is supplied.

    The coastline is shrunk once by ``min_inland_km`` (in degrees) and prepared, mirroring the
    land filter in ``core.encounters``. A point inside the shrunken polygon is at least that far
    from the coast, so the test itself is a single point-in-polygon query rather than a distance
    calculation per fix. Buffer and validity failures leave the detector land-blind instead of
    aborting a whole snapshot run.
    """
    if land is None or land.is_empty or not pings:
        return set()
    try:
        inner = land.buffer(-min_inland_km / KM_PER_DEG)
    except (GEOSException, ValueError):
        return set()
    if inner.is_empty:
        return set()
    prepare(inner)  # in-place in shapely 2; contains_xy below then uses the index
    inside = contains_xy(inner, [p.lon for p in pings], [p.lat for p in pings])
    return {p for p, hit in zip(pings, inside, strict=True) if bool(hit)}


def _merge_cells(seeds: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Merge candidate cells that touch (including diagonally) into connected components.

    One ghost point can straddle a cell boundary, and the fixture's is spread over four cells.
    Merging first means the event reports the point once; the exact disk search below then picks
    the real shared position inside the component.
    """
    components: list[list[tuple[int, int]]] = []
    seen: set[tuple[int, int]] = set()
    for seed in sorted(seeds):
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        component: list[tuple[int, int]] = []
        while stack:
            cell = stack.pop()
            component.append(cell)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neighbour = (cell[0] + dy, cell[1] + dx)
                    if neighbour in seeds and neighbour not in seen:
                        seen.add(neighbour)
                        stack.append(neighbour)
        components.append(sorted(component))
    return components


def _pings_near(
    lat: float,
    lon: float,
    radius_km: float,
    cells: Mapping[tuple[int, int], list[Ping]],
    rows: int,
    cols: int,
) -> list[Ping]:
    """Every ping within ``radius_km`` of a position, scanning only the surrounding cells."""
    out: list[Ping] = []
    for neighbour in _neighbour_cells(_cell(lat, lon), rows, cols):
        for ping in cells.get(neighbour, ()):
            if distance_km(lat, lon, ping.lat, ping.lon) <= radius_km:
                out.append(ping)
    return out


def _densest_disk(
    component: Sequence[Ping],
    cells: Mapping[tuple[int, int], list[Ping]],
    rows: int,
    cols: int,
    radius_km: float,
    min_vessels: int,
) -> tuple[list[Ping], float, float] | None:
    """The disk of ``radius_km`` holding the most distinct MMSIs, or None below the threshold.

    Every fix is tried as the centre of a disk. Using a reported position as the anchor (rather
    than a free grid centre) guarantees the disk is anchored on real data, while the returned
    centre is the mean of the reports inside it, which is stable for a jittery spoofed fix. Ties
    go to the earliest report so two runs over the same recording produce the same event.
    """
    component_vessels = len({p.mmsi for p in component})
    best: list[Ping] = []
    best_count = -1
    for anchor in sorted(component, key=lambda p: (p.t, p.mmsi, p.lat, p.lon)):
        nearby = _pings_near(anchor.lat, anchor.lon, radius_km, cells, rows, cols)
        count = len({p.mmsi for p in nearby})
        if count > best_count:
            best = nearby
            best_count = count
            if best_count == component_vessels:
                # Cannot beat the number of vessels in the whole component; stop early.
                break
    if best_count < min_vessels or not best:
        return None
    lat = sum(p.lat for p in best) / len(best)
    lon = sum(p.lon for p in best) / len(best)
    return best, lat, lon


def _depth_km(land: BaseGeometry, lat: float, lon: float) -> float:
    """Approximate distance from a point inside land to the nearest coastline, in km."""
    try:
        return float(land.boundary.distance(Point(lon, lat))) * KM_PER_DEG
    except (GEOSException, ValueError):
        return 0.0


def _fmt_duration(minutes: float) -> str:
    """Human duration such as '4 h 47 min', matching how the UI talks about gaps."""
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def _severity(n_vessels: int, on_land: bool) -> float:
    """Severity from the number of unrelated ships, plus a land bonus, capped at one.

    Six independent ships at one point saturates the sharing evidence; below that the score is
    proportional. Land is an extra, not a substitute: a point where ships cannot be is stronger
    than a point where they merely should not be, so it adds 0.2 to whatever sharing found.
    """
    sharing = min(1.0, n_vessels / 6.0)
    return round(min(1.0, sharing + (0.2 if on_land else 0.0)), 3)


def _leg_speed(a: Ping, b: Ping) -> float | None:
    """Implied speed from ``a`` to ``b`` in knots, or None when the two fixes share an instant."""
    seconds = (b.t - a.t).total_seconds()
    if seconds <= 0:
        return None
    return _implied_kn(distance_km(a.lat, a.lon, b.lat, b.lon), seconds)


def _track_neighbours(track: Sequence[Ping], reports: Sequence[Ping]) -> tuple[Ping | None, Ping | None]:
    """The fixes just before the first visit and just after the last visit in a track."""
    ordered = sorted(track, key=lambda p: p.t)
    first, last = reports[0], reports[-1]
    before = after = None
    for index, ping in enumerate(ordered):
        if ping is first or ping == first:
            before = ordered[index - 1] if index > 0 else None
            break
    for index, ping in enumerate(ordered):
        if ping is last or ping == last:
            after = ordered[index + 1] if index + 1 < len(ordered) else None
            break
    return before, after


def _event_from_core(
    core: Sequence[Ping],
    lat: float,
    lon: float,
    tracks: Mapping[int, Sequence[Ping]],
    land: BaseGeometry | None,
    min_inland_km: float,
) -> Event:
    """Turn one cluster's reports into the single Event the UI shows for that ghost point."""
    by_mmsi: dict[int, list[Ping]] = {}
    for ping in core:
        by_mmsi.setdefault(ping.mmsi, []).append(ping)

    vessels: list[dict[str, Any]] = []
    for mmsi, reports in by_mmsi.items():
        reports = sorted(reports, key=lambda p: p.t)
        before, after = _track_neighbours(tracks.get(mmsi, reports), reports)
        jump_in = _leg_speed(before, reports[0]) if before is not None else None
        jump_out = _leg_speed(reports[-1], after) if after is not None else None
        offsets = [distance_km(lat, lon, ping.lat, ping.lon) for ping in (before, after) if ping is not None]
        vessels.append(
            {
                "mmsi": mmsi,
                "n_reports": len(reports),
                "first": reports[0].t,
                "last": reports[-1].t,
                "jump_in_kn": round(jump_in, 1) if jump_in is not None else None,
                "jump_out_kn": round(jump_out, 1) if jump_out is not None else None,
                "offset_km": round(min(offsets), 1) if offsets else 0.0,
            }
        )
    vessels.sort(key=lambda row: (row["first"], row["mmsi"]))

    start: datetime = min(row["first"] for row in vessels)
    end: datetime = max(row["last"] for row in vessels)
    n_vessels = len(vessels)

    inland = _inland_pings(core, land, min_inland_km)
    on_land = bool(inland)
    inland_km = 0.0
    if on_land and land is not None:
        centre_inside = Point(lon, lat).within(land)
        inland_km = _depth_km(land, lat, lon) if centre_inside else 0.0
        if inland_km <= 0.0:
            inland_km = max(_depth_km(land, p.lat, p.lon) for p in inland)
    inland_km = round(inland_km, 1)

    offsets = [row["offset_km"] for row in vessels if row["offset_km"] > 0.0]
    offset_km = float(median(offsets)) if offsets else 0.0
    minutes = (end - start).total_seconds() / 60.0
    span = _fmt_duration(minutes)
    if n_vessels == 1:
        subject = "1 ship jumped to this point and back"
    else:
        subject = f"{n_vessels} unrelated ships each jumped to the same point and back"
    explanation = (
        f"{subject} ({offset_km:.0f} km from their real tracks) within {span}; ships cannot "
        "travel that fast, so their satellite positions were falsified (GNSS spoofing) or a "
        "shared device is wrong."
    )
    if on_land:
        # Say only what is verified: the coarse 50 m coastline has no large lakes, so a km figure
        # would overstate (the real St Petersburg point is ~25 km from Lake Ladoga, not 53).
        explanation += " The point is on land."

    evidence: dict[str, Any] = {
        "vessels": [
            {
                "mmsi": row["mmsi"],
                "n_reports": row["n_reports"],
                "first": row["first"].isoformat(),
                "last": row["last"].isoformat(),
                "jump_in_kn": row["jump_in_kn"],
                "jump_out_kn": row["jump_out_kn"],
                "offset_km": row["offset_km"],
            }
            for row in vessels
        ],
        "n_vessels": n_vessels,
        "on_land": on_land,
        "inland_km": inland_km,
        "centre": [round(lat, 5), round(lon, 5)],
    }

    return Event(
        kind="spoof",
        mmsi=int(vessels[0]["mmsi"]),
        start=start,
        end=end,
        lat=round(lat, 5),
        lon=round(lon, 5),
        severity=_severity(n_vessels, on_land),
        explanation=explanation,
        evidence=evidence,
    )


def _segment_offset_km(track: Sequence[Ping], segment: Sequence[Ping]) -> float:
    """How far the teleported visit lies from the vessel's real track (the fixes before and after)."""
    ordered = list(track)
    i = ordered.index(segment[0])
    j = ordered.index(segment[-1])
    ends = [ordered[k] for k in (i - 1, j + 1) if 0 <= k < len(ordered)]
    if not ends:
        return 0.0
    return min(distance_km(segment[0].lat, segment[0].lon, e.lat, e.lon) for e in ends)


def ghost_points(
    tracks: Mapping[int, Sequence[Ping]],
    *,
    land: BaseGeometry | None = None,
    radius_m: float = 500,
    min_vessels: int = 3,
    min_inland_km: float = 1.0,
    min_offset_km: float = 20.0,
) -> list[Event]:
    """Find points that several unrelated vessels teleported to and back from.

    Only vessels that themselves made an impossible out-and-back detour of at least
    ``min_offset_km`` from their real track are counted and listed. (Found on real data: a busy
    river berth on the Neva, which the coarse coastline calls land, collected normal traffic plus
    a few GPS jitters of 1-15 km; real spoofing displaced ships 48 km.)

    ``tracks`` is the output of :func:`darkfleet.core.kinematics.split_tracks`: clean, time-sorted
    pings per MMSI. Vessels that never make an impossible out-and-back detour contribute nothing,
    so a port or an anchorage cannot produce an event however many hulls share the position.

    Teleport-outlier fixes are grid-bucketed at :data:`BUCKET_DEG`; only cells whose neighbourhood
    holds ``min_vessels`` distinct ships are visited, so cost grows with the number of outlier
    cells rather than with the square of the fleet. A candidate component becomes an event when
    its densest ``radius_m`` disk holds at least ``min_vessels`` distinct MMSIs among the outlier
    fixes; every report inside that disk then joins the event's vessel list.

    ``land`` never triggers an event. When supplied, fixes more than ``min_inland_km`` inside the
    coastline are reported as extra evidence (``on_land`` / ``inland_km``) on an event that the
    out-and-back rule already found.

    Returns one Event per ghost point, with the first vessel (earliest first report) in ``mmsi``,
    the cluster centre, the first/last report there, and per-vessel jump speeds in ``evidence``.
    """
    pings = [ping for track in tracks.values() for ping in track]
    if not pings:
        return []

    all_cells: dict[tuple[int, int], list[Ping]] = {}
    for ping in pings:
        all_cells.setdefault(_cell(ping.lat, ping.lon), []).append(ping)

    radius_km = radius_m / 1000.0
    outliers = []
    for track in tracks.values():
        for segment in _teleport_segments(track, radius_km):
            if _segment_offset_km(track, segment) >= min_offset_km:
                outliers.extend(segment)
    if not outliers:
        return []

    outlier_cells: dict[tuple[int, int], list[Ping]] = {}
    for ping in outliers:
        outlier_cells.setdefault(_cell(ping.lat, ping.lon), []).append(ping)

    max_abs_lat = max(abs(ping.lat) for ping in outliers)
    rows, cols = _neighbour_extent(radius_m, max_abs_lat)

    seeds: set[tuple[int, int]] = set()
    for cell in outlier_cells:
        heard = {
            ping.mmsi
            for neighbour in _neighbour_cells(cell, rows, cols)
            for ping in outlier_cells.get(neighbour, ())
        }
        if len(heard) >= min_vessels:
            seeds.add(cell)

    events: list[Event] = []
    for component in _merge_cells(seeds):
        component_pings = [ping for cell in component for ping in outlier_cells[cell]]
        core = _densest_disk(
            component_pings,
            outlier_cells,
            rows,
            cols,
            radius_km,
            min_vessels,
        )
        if core is None:
            continue
        _, lat, lon = core
        # Once several independent hulls have teleported to this point, every fix inside the
        # disk belongs to the same physical ghost: the position itself is what is falsified.
        nearby = _pings_near(lat, lon, radius_km, all_cells, rows, cols)
        # Count and list only vessels that teleported here, never ordinary traffic at the same spot.
        teleported = {ping.mmsi for ping in component_pings}
        nearby = [ping for ping in nearby if ping.mmsi in teleported]
        if len({ping.mmsi for ping in nearby}) < min_vessels:
            continue
        events.append(_event_from_core(nearby, lat, lon, tracks, land, min_inland_km))

    return sorted(events, key=lambda event: (event.start, event.mmsi, event.lat, event.lon))


def filter_explained_jumps(jumps: list[Event], ghosts: list[Event], km: float = 1.0) -> list[Event]:
    """Drop jump events that a spoofed ghost point already explains.

    A jump is an impossible speed between two consecutive fixes. When one of those fixes is the
    ghost point, the jump is not a second finding: the falsified position caused it, and reporting
    both would double-count one cause. The jump Event stores the impossible fix in ``lat``/``lon``;
    some producers also keep both ends in evidence, so those pairs are checked too. Events within
    ``km`` of any ghost centre are removed; the original order is preserved.
    """
    centres = [(ghost.lat, ghost.lon) for ghost in ghosts if ghost.kind == "spoof"]
    if not centres:
        return list(jumps)

    def positions(event: Event) -> list[tuple[float, float]]:
        """The event position plus any start/end pair a producer put in evidence."""
        out = [(event.lat, event.lon)]
        for lat_key, lon_key in (
            ("last_lat", "last_lon"),
            ("next_lat", "next_lon"),
            ("start_lat", "start_lon"),
            ("end_lat", "end_lon"),
        ):
            lat = event.evidence.get(lat_key)
            lon = event.evidence.get(lon_key)
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                out.append((float(lat), float(lon)))
        return out

    kept: list[Event] = []
    for jump in jumps:
        if jump.kind != "jump":
            kept.append(jump)
            continue
        explained = any(
            distance_km(lat, lon, ghost_lat, ghost_lon) <= km
            for lat, lon in positions(jump)
            for ghost_lat, ghost_lon in centres
        )
        if not explained:
            kept.append(jump)
    return kept
