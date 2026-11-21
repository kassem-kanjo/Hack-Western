"""Detect meetings between vessels: pairs that sit together, and pairs that go dark together.

Two kinds of evidence, because "met" means different things depending on what the transponders
did:

* A visible **meeting** (kind ``"meeting"``) is a pair we can watch: both vessels report from the
  same patch of water at slow speed for long enough that it is not a passing encounter. We
  resample each track onto one shared 5-minute clock because raw AIS rates differ per vessel and
  per receiver, and a pair can only be compared at instants both vessels actually have. The
  nearest ping within 10 minutes stands in for the step (a ship's position does not change much
  in 10 minutes at these speeds, and a longer reach would invent encounters that never happened).
* A **dark meeting** (kind ``"dark_meeting"``) is a pair we cannot see: two silence events whose
  space-time prisms overlap. Only the physics can say whether two invisible ships could have met,
  so we hand each prism to :func:`darkfleet.core.match.rendezvous_windows` and report the overlap.

Pure functions only: tracks, silence events and land are passed in; nothing here reads files.
"""

from __future__ import annotations

import itertools
import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .geo import Fix, distance_km
from .match import rendezvous_windows
from .speeds import max_speed_knots
from .tracks import Event, Ping

STEP_MINUTES = 5
"""Resampling clock for visible meetings; both vessels are compared at these instants only."""

NEAREST_PING_MINUTES = 10
"""A step with no ping this close is treated as "no position": the vessel is not compared."""

CELL_DEG = 0.01
"""Grid cell size for neighbour lookup (~1.1 km of latitude, ~0.57 km of longitude at 59 N).
The neighbourhood is sized from `max_m` at each step, so a wider search distance does not silently
miss pairs; at the default 500 m it degenerates to the expected 3x3 block."""

LAND_CLEAR_KM = 3.0
"""Meetings closer than this to land are skipped (harbour traffic, not a rendezvous)."""

KM_PER_DEG = 111.0
"""Rough degrees-per-km conversion for the shapely land distance; the spec allows this order."""


@dataclass
class _Run:
    """A maximal run of consecutive steps in which one pair satisfies the meeting test."""

    start: datetime
    end: datetime
    min_m: float
    lat: float
    lon: float
    sogs: list[float] = field(default_factory=list)

    def add(self, t: datetime, dist_m: float, lat: float, lon: float, sog_a: float, sog_b: float) -> None:
        self.end = t
        self.sogs.extend((sog_a, sog_b))
        if dist_m < self.min_m:
            self.min_m = dist_m
            self.lat, self.lon = lat, lon


def _sorted_tracks(tracks: Mapping[int, Sequence[Ping]]) -> dict[int, list[Ping]]:
    """Copy each track sorted by time (deduplicating nothing: the caller owns track cleaning)."""
    return {mmsi: sorted(pings, key=lambda p: p.t) for mmsi, pings in tracks.items() if pings}


def _step_times(tracks: Mapping[int, Sequence[Ping]]) -> list[datetime]:
    """One shared 5-minute clock from the first ping to the last, aligned to the wall clock.

    Aligning to 5-minute boundaries (rather than to each vessel's first ping) is what lets a pair
    be compared at the same instants across tracks. The clock never runs past the data: a step
    after the last ping would otherwise let a finished track linger via the 10-minute tolerance
    and stretch a 20-minute meeting past the 30-minute threshold.
    """
    first = min(pings[0].t for pings in tracks.values())
    last = max(pings[-1].t for pings in tracks.values())
    if last < first:
        return []
    start = first.replace(minute=first.minute - first.minute % STEP_MINUTES, second=0, microsecond=0)
    step = timedelta(minutes=STEP_MINUTES)
    times: list[datetime] = []
    t = start
    while t <= last:
        times.append(t)
        t += step
    return times


def _nearest(pings: Sequence[Ping], times: Sequence[datetime], t: datetime) -> Ping | None:
    """The ping closest to `t`, provided it is within NEAREST_PING_MINUTES; else None."""
    i = bisect_left(times, t)
    best: Ping | None = None
    best_gap = float("inf")
    for j in (i - 1, i):
        if not 0 <= j < len(pings):
            continue
        gap = abs((times[j] - t).total_seconds()) / 60.0
        if gap <= NEAREST_PING_MINUTES and gap < best_gap:
            best, best_gap = pings[j], gap
    return best


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return int(lat // CELL_DEG), int(lon // CELL_DEG)


def _offsets(max_m: float, lats: Sequence[float]) -> list[tuple[int, int]]:
    """Cell offsets to compare against: self plus the forward half of a `max_m`-wide block.

    The block is grown from the requested separation, because `max_m` is a sweepable validation
    parameter and a fixed 3x3 neighbourhood would silently miss wider pairs. Longitude cells are
    narrower than latitude cells away from the equator, so the column reach is computed from the
    highest latitude present at the step. The cos clamp keeps a polar input from exploding the
    neighbourhood; the Baltic is nowhere near it. Only the forward half is returned so each
    unordered pair of cells is visited once, which is what keeps the scan off O(n^2).
    """
    cell_m = CELL_DEG * 111_000.0
    rows = max(1, math.ceil(max_m / cell_m))
    cos_lat = max(0.1, math.cos(math.radians(max(abs(lat) for lat in lats))))
    cols = max(1, math.ceil(max_m / (cell_m * cos_lat)))
    return [(0, 0)] + [
        (dy, dx) for dy in range(-rows, rows + 1) for dx in range(-cols, cols + 1) if (dy, dx) > (0, 0)
    ]


def _pair(mmsi_a: int, mmsi_b: int) -> tuple[int, int]:
    """Ordered key: the spec wants the lower MMSI first, and each pair counted once."""
    return (mmsi_a, mmsi_b) if mmsi_a < mmsi_b else (mmsi_b, mmsi_a)


def _offshore(land: BaseGeometry, p: Ping) -> bool:
    """True if the ping is more than LAND_CLEAR_KM from land.

    Distance is measured in degrees (shapely's planar units) and converted with 111 km/deg, as
    the spec allows: a coastline finer than that is below the resolution of this detector anyway.
    """
    return land.distance(Point(p.lon, p.lat)) * KM_PER_DEG > LAND_CLEAR_KM


def _finish(key: tuple[int, int], run: _Run, max_m: float, min_minutes: float) -> Event | None:
    """Turn a finished run into an Event, or None if it was shorter than `min_minutes`."""
    minutes = (run.end - run.start).total_seconds() / 60.0
    if minutes + 1e-9 < min_minutes:
        return None
    mean_sog = sum(run.sogs) / len(run.sogs) if run.sogs else 0.0
    # Severity grows with how close and how long the pair stayed together, each saturating at a
    # level that is already unambiguous (touching distance, twice the minimum duration).
    proximity = max(0.0, min(1.0, 1.0 - run.min_m / max_m)) if max_m > 0 else 1.0
    duration = min(1.0, minutes / (2.0 * min_minutes)) if min_minutes > 0 else 1.0
    severity = round(0.5 * proximity + 0.5 * duration, 3)
    event = Event(
        kind="meeting",
        mmsi=key[0],
        other_mmsi=key[1],
        start=run.start,
        end=run.end,
        lat=run.lat,
        lon=run.lon,
        severity=severity,
        explanation=(
            f"Two vessels stayed within {run.min_m:.0f} m of each other at {mean_sog:.1f} kn "
            f"for {minutes:.0f} min."
        ),
        evidence={
            "min_distance_m": round(run.min_m, 1),
            "minutes": round(minutes, 1),
            "mean_sog": round(mean_sog, 2),
        },
    )
    return event


def meetings(
    tracks: Mapping[int, Sequence[Ping]],
    *,
    max_m: float = 500,
    max_sog: float = 3.0,
    min_minutes: int = 30,
    land: BaseGeometry | None = None,
) -> list[Event]:
    """Visible meetings: a maximal slow-and-close run of at least `min_minutes`.

    Each track is resampled to 5-minute steps using its nearest ping within 10 minutes. At every
    step vessels are bucketed into ~0.01-degree cells and each cell is compared only with a small
    block of neighbouring cells sized from `max_m`, so cost scales with the number of occupied
    cells rather than with the square of the fleet. A pair qualifies at a step when both have a
    position, both SOGs are known and <= `max_sog`, and the geodesic separation is <= `max_m`.
    Both positions must also be more than 3 km from `land` when a land geometry is supplied;
    near-shore clustering is port traffic, not a rendezvous. A run ends as soon as either
    condition fails, so only contiguous minutes count.

    Returns one Event per meeting, with the lower MMSI in `mmsi`; evidence holds the closest
    approach (metres), the run length (minutes) and the mean SOG (knots) of both vessels.
    """
    series = _sorted_tracks(tracks)
    if len(series) < 2:
        return []

    times = _step_times(series)
    if not times:
        return []
    resampled: dict[int, list[Ping | None]] = {}
    for mmsi, pings in series.items():
        stamps = [p.t for p in pings]
        resampled[mmsi] = [_nearest(pings, stamps, t) for t in times]

    runs: dict[tuple[int, int], _Run] = {}
    events: list[Event] = []
    for index, t in enumerate(times):
        buckets: dict[tuple[int, int], list[Ping]] = {}
        for pings in resampled.values():
            p = pings[index]
            if p is None or p.sog is None or p.sog > max_sog:
                continue
            if land is not None and not land.is_empty and not _offshore(land, p):
                continue
            buckets.setdefault(_cell(p.lat, p.lon), []).append(p)

        step_pairs: dict[tuple[int, int], tuple[float, Ping, Ping]] = {}
        offsets = _offsets(max_m, [p.lat for here in buckets.values() for p in here]) if buckets else ()
        for (cy, cx), here in buckets.items():
            for dy, dx in offsets:
                there = buckets.get((cy + dy, cx + dx))
                if not there:
                    continue
                pairs = (
                    itertools.combinations(here, 2) if (dy, dx) == (0, 0) else itertools.product(here, there)
                )
                for a, b in pairs:
                    key = _pair(a.mmsi, b.mmsi)
                    if key in step_pairs:
                        continue
                    dist_m = distance_km(a.lat, a.lon, b.lat, b.lon) * 1000.0
                    if dist_m <= max_m:
                        step_pairs[key] = (dist_m, a, b)

        for key in [k for k in runs if k not in step_pairs]:
            event = _finish(key, runs.pop(key), max_m, min_minutes)
            if event is not None:
                events.append(event)

        for key, (dist_m, a, b) in step_pairs.items():
            lat, lon = (a.lat + b.lat) / 2.0, (a.lon + b.lon) / 2.0
            run = runs.get(key)
            if run is None:
                runs[key] = _Run(t, t, dist_m, lat, lon, [a.sog, b.sog])
            else:
                run.add(t, dist_m, lat, lon, a.sog, b.sog)

    for key, run in runs.items():
        event = _finish(key, run, max_m, min_minutes)
        if event is not None:
            events.append(event)

    return sorted(events, key=lambda e: (e.start, e.mmsi, e.other_mmsi or 0))


def _cap_for(caps: Mapping[int, float | str], mmsi: int) -> float:
    """Speed cap (knots) for one vessel: a class name, a numeric cap, or the default class."""
    value = caps.get(mmsi)
    if value is None:
        return max_speed_knots(None)
    if isinstance(value, str):
        return max_speed_knots(value)
    return float(value)


def _bounding_gap(
    event: Event, tracks: Mapping[int, Sequence[Ping]], cap_kn: float
) -> tuple[Fix, Fix, float] | None:
    """The last ping before the silence and the first after it, as a prism input.

    The silence detector already decided *when* the vessel was dark; the prism needs the two
    visible fixes on either side. If the track does not bracket the silence, we cannot build a
    prism and the event is skipped rather than guessed at.
    """
    pings = tracks.get(event.mmsi)
    if not pings:
        return None
    ordered = sorted(pings, key=lambda p: p.t)
    times = [p.t for p in ordered]
    before = bisect_right(times, event.start) - 1
    after = bisect_left(times, event.end)
    if before < 0 or after >= len(ordered):
        return None
    a, b = ordered[before], ordered[after]
    if b.t <= a.t:
        return None
    return (Fix(a.lat, a.lon, a.t), Fix(b.lat, b.lon, b.t), cap_kn)


def dark_meetings(
    silence_events: Sequence[Event],
    tracks: Mapping[int, Sequence[Ping]],
    caps: Mapping[int, float | str],
) -> list[Event]:
    """Pairs of silence events whose prisms overlap in time and space.

    For each silence we reconstruct the bounding pings from `tracks`, turn that gap into a prism
    and ask :func:`rendezvous_windows` where the two prisms intersect. One Event is emitted per
    vessel pair (the best-overlap window, since a pair either met or did not); its evidence
    carries the overlap area, the window length and how many windows were found. `caps` maps an
    MMSI to a speed cap in knots or to a :mod:`darkfleet.core.speeds` class name; missing MMSIs
    get the default class cap, which is deliberately conservative.

    Only pairs whose silence intervals overlap in time are considered: two ships cannot meet
    while one of them is transmitting.
    """
    gaps: list[tuple[Event, tuple[Fix, Fix, float]]] = []
    for event in silence_events:
        if event.kind != "silence":
            continue
        gap = _bounding_gap(event, tracks, _cap_for(caps, event.mmsi))
        if gap is not None:
            gaps.append((event, gap))

    events: list[Event] = []
    for (event_a, gap_a), (event_b, gap_b) in itertools.combinations(gaps, 2):
        if event_a.mmsi == event_b.mmsi:
            continue
        if max(event_a.start, event_b.start) > min(event_a.end, event_b.end):
            continue
        windows = rendezvous_windows(gap_a, gap_b)
        if not windows:
            continue
        best = max(windows, key=lambda w: w.max_overlap_km2)
        key = _pair(event_a.mmsi, event_b.mmsi)
        minutes = (best.end - best.start).total_seconds() / 60.0
        # Area saturates at 1000 km^2 (a large, unambiguous overlap) and time at two hours;
        # both are heuristics for "how much room and time the two dark ships shared".
        area_score = min(1.0, best.max_overlap_km2 / 1000.0)
        time_score = min(1.0, minutes / 120.0)
        severity = round(0.5 * area_score + 0.5 * time_score, 3)
        events.append(
            Event(
                kind="dark_meeting",
                mmsi=key[0],
                other_mmsi=key[1],
                start=best.start,
                end=best.end,
                lat=best.centroid_lat,
                lon=best.centroid_lon,
                severity=severity,
                explanation=(
                    f"Two vessels were dark at the same time and place: their possible areas "
                    f"overlapped by up to {best.max_overlap_km2:.0f} km² for {minutes:.0f} min."
                ),
                evidence={
                    "max_overlap_km2": round(best.max_overlap_km2, 3),
                    "minutes": round(minutes, 1),
                    "windows": len(windows),
                },
            )
        )
    return sorted(events, key=lambda e: (e.start, e.mmsi, e.other_mmsi or 0))
