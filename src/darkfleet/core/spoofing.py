"""Find ghost points: places where several unrelated ships report the same impossible position.

A single AIS fix can be wrong for many boring reasons (a dropped message, a slow update). When
several *different* MMSIs report the same tight position, that reason stops being boring: either
their satellite receivers were fed the same false coordinates (GNSS spoofing) or one shared device
is writing the wrong position into several transponders. The recorded St Petersburg case is the
canonical example: six ships that otherwise sail normal tracks all appear on a patch of land about
25 km from the city at different times in one afternoon.

The detector has two independent signals, because the data does not always allow both:

* **Sharing.** Pings are bucketed into coarse cells (~0.005 degrees, about 550 m north-south). A
  cell whose neighbourhood holds at least ``min_vessels`` distinct MMSIs is a candidate; adjacent
  candidates are merged so one physical point becomes one event rather than one event per cell.
* **Land.** With a coastline, a fix more than ``min_inland_km`` inside land is impossible for a
  ship. The coastline is buffered inwards once and prepared, so the test is a cheap point query.

Sharing alone is also how busy water looks. A shipping lane, an anchorage or a river approach can
put many different ships within 500 m of the same spot over a few hours, so a shared water point is
not by itself evidence of spoofing. A shared cluster is therefore kept only when at least one of the
reporting vessels is otherwise nowhere near it (:data:`NORMAL_TRAFFIC_KM`): a normal passage has
the same ships reporting just before or after, while a spoofed fix is a detour away from the ship's
own track. Land evidence does not need that test, because a ship cannot be inland at all.

Pure: tracks and an optional land geometry go in; events come out. Nothing here reads a file.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from shapely import contains_xy, prepare
from shapely.errors import GEOSException
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .geo import distance_km
from .tracks import Event, Ping

BUCKET_DEG = 0.005
"""Grid cell size for sharing candidates (~550 m of latitude, less of longitude at 60 N)."""

KM_PER_DEG = 111.0
"""Coarse degrees-per-km conversion, the same approximation ``core.encounters`` uses."""

NORMAL_TRAFFIC_KM = 2.0
"""A vessel reporting this close to a shared point is ordinary traffic passing through it.

A false ghost point is usually a lane, a harbour mouth or an anchorage: every ship that reports
there also reports a few hundred metres away a minute earlier or later. A spoofed fix is a detour
away from the vessel's own track, so at least one ship has no other report anywhere near it.
"""


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


def _is_isolated(track: Sequence[Ping], lat: float, lon: float, radius_km: float) -> bool:
    """True when this vessel has no other report within :data:`NORMAL_TRAFFIC_KM` of the point.

    Reports inside the ghost disk are the evidence itself and are ignored. A vessel with no other
    report at all is isolated by absence of evidence: it appears only at this point, which is
    exactly the single-transmitter or shared-device case. The question is whether the cluster is
    part of a continuous passage, not how long the vessel has been transmitting elsewhere.
    """
    others = [ping for ping in track if distance_km(lat, lon, ping.lat, ping.lon) > radius_km]
    if not others:
        return True
    return min(distance_km(lat, lon, ping.lat, ping.lon) for ping in others) > NORMAL_TRAFFIC_KM


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


def _event_from_core(
    core: Sequence[Ping],
    lat: float,
    lon: float,
    land: BaseGeometry | None,
    inland: set[Ping],
) -> Event:
    """Turn one cluster's reports into the single Event the UI shows for that ghost point."""
    by_mmsi: dict[int, list[Ping]] = {}
    for ping in core:
        by_mmsi.setdefault(ping.mmsi, []).append(ping)

    vessels: list[dict[str, Any]] = []
    for mmsi, reports in by_mmsi.items():
        reports = sorted(reports, key=lambda p: p.t)
        vessels.append(
            {
                "mmsi": mmsi,
                "n_reports": len(reports),
                "first": reports[0].t,
                "last": reports[-1].t,
            }
        )
    vessels.sort(key=lambda row: (row["first"], row["mmsi"]))

    start: datetime = min(row["first"] for row in vessels)
    end: datetime = max(row["last"] for row in vessels)
    n_vessels = len(vessels)

    land_core = [p for p in core if p in inland]
    on_land = bool(land_core)
    inland_km = 0.0
    if on_land and land is not None:
        centre_inside = Point(lon, lat).within(land)
        inland_km = _depth_km(land, lat, lon) if centre_inside else 0.0
        if inland_km <= 0.0:
            inland_km = max(_depth_km(land, p.lat, p.lon) for p in land_core)
    inland_km = round(inland_km, 1)

    minutes = (end - start).total_seconds() / 60.0
    span = _fmt_duration(minutes)
    subject = "1 ship" if n_vessels == 1 else f"{n_vessels} unrelated ships"
    if on_land:
        explanation = (
            f"{subject} reported the same point {inland_km:.0f} km inland "
            f"within {span}; ships cannot be there, so their satellite positions were falsified "
            "(GNSS spoofing) or a shared device is wrong."
        )
    else:
        explanation = (
            f"{subject} reported the same point within {span}, far from their "
            "own tracks; a shared device is wrong or their satellite positions were falsified "
            "(GNSS spoofing)."
        )

    evidence: dict[str, Any] = {
        "vessels": [
            {
                "mmsi": row["mmsi"],
                "n_reports": row["n_reports"],
                "first": row["first"].isoformat(),
                "last": row["last"].isoformat(),
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


def ghost_points(
    tracks: Mapping[int, Sequence[Ping]],
    *,
    land: BaseGeometry | None = None,
    radius_m: float = 500,
    min_vessels: int = 3,
    min_inland_km: float = 1.0,
) -> list[Event]:
    """Find positions that several unrelated vessels report, or that lie well inside land.

    ``tracks`` is the output of :func:`darkfleet.core.kinematics.split_tracks`: clean, time-sorted
    pings per MMSI. The scan is grid-bucketed at :data:`BUCKET_DEG` and only cells whose
    neighbourhood holds ``min_vessels`` distinct ships (or an inland fix) are visited, so the cost
    grows with the number of occupied cells rather than with the square of the fleet.

    A candidate cell cluster becomes an event when its densest ``radius_m`` disk still holds at
    least ``min_vessels`` distinct MMSIs and at least one of those vessels is not otherwise within
    :data:`NORMAL_TRAFFIC_KM` of the point. That last condition is what separates a spoofed fix
    from a shipping lane where the same fleet passes every day; it is skipped for land evidence,
    which is impossible regardless of traffic. With ``land`` supplied, a fix more than
    ``min_inland_km`` inside the coastline is reported even if only one ship made it.

    Returns one Event per ghost point, with the first vessel (earliest first report) in ``mmsi``,
    the cluster centre, the first/last report there, and per-vessel counts in ``evidence``.
    """
    pings = [ping for track in tracks.values() for ping in track]
    if not pings:
        return []

    cells: dict[tuple[int, int], list[Ping]] = {}
    for ping in pings:
        cells.setdefault(_cell(ping.lat, ping.lon), []).append(ping)

    inland = _inland_pings(pings, land, min_inland_km)
    max_abs_lat = max(abs(ping.lat) for ping in pings)
    rows, cols = _neighbour_extent(radius_m, max_abs_lat)

    # A cell is a candidate when its neighbourhood alone already holds enough distinct ships, or
    # when it contains a fix that is more than min_inland_km inside land.
    seeds: set[tuple[int, int]] = set()
    for cell, here in cells.items():
        heard = {
            ping.mmsi for neighbour in _neighbour_cells(cell, rows, cols) for ping in cells.get(neighbour, ())
        }
        if len(heard) >= min_vessels:
            seeds.add(cell)
        if any(ping in inland for ping in here):
            seeds.add(cell)

    events: list[Event] = []
    for component in _merge_cells(seeds):
        component_pings = [ping for cell in component for ping in cells[cell]]
        core = _densest_disk(
            component_pings,
            cells,
            rows,
            cols,
            radius_m / 1000.0,
            min_vessels,
        )
        if core is not None:
            disk, lat, lon = core
            # Sharing on water is only suspicious when a reporting ship has no business there.
            if any(_is_isolated(tracks.get(p.mmsi, ()), lat, lon, radius_m / 1000.0) for p in disk):
                events.append(_event_from_core(disk, lat, lon, land, inland))
                continue

        land_pings = [ping for ping in component_pings if ping in inland]
        if land_pings:
            lat = sum(p.lat for p in land_pings) / len(land_pings)
            lon = sum(p.lon for p in land_pings) / len(land_pings)
            events.append(_event_from_core(land_pings, lat, lon, land, inland))

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
