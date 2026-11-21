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
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shapely import intersects_xy, prepare
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

MOORED_NAV_STATUS = 5
"""AIS navigational status 5: the vessel is moored. A berthed pair is not a rendezvous."""

TWIN_MAX_M = 5.0
"""A pair never more than this far apart is one hull broadcasting two MMSIs, not two ships."""

OFFSHORE_FULL_KM = 20.0
"""Offshore evidence saturates here: beyond 20 km from land, proximity says nothing more."""

SEVERITY_FLOOR = 0.3
"""Duration component at the minimum qualifying duration (30 min by default)."""

SEVERITY_FULL_MINUTES = 180.0
"""Duration component reaches 1.0 at three hours, the issue's unambiguous meeting length."""

ShipTypeFilter = Callable[[int | None], bool] | Collection[int]
"""Allowed AIS ship types: a predicate over a type code, or a collection of allowed codes."""


@dataclass
class _Run:
    """A maximal run of consecutive steps in which one pair satisfies the meeting test."""

    start: datetime
    end: datetime
    min_m: float
    lat: float
    lon: float
    sogs: list[float] = field(default_factory=list)
    max_m: float = 0.0

    def add(self, t: datetime, dist_m: float, lat: float, lon: float, sog_a: float, sog_b: float) -> None:
        self.end = t
        self.sogs.extend((sog_a, sog_b))
        if dist_m < self.min_m:
            self.min_m = dist_m
            self.lat, self.lon = lat, lon
        if dist_m > self.max_m:
            self.max_m = dist_m


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


def _prepare_land(land: BaseGeometry | None, min_offshore_km: float) -> BaseGeometry | None:
    """Land buffered by the exclusion zone, prepared once for every time step in the call.

    Buffering in degrees (km / 111) is the same approximation the detector already accepted: a
    coastline finer than a kilometre is below its resolution. Preparing the geometry once makes
    each step's vectorised ``intersects_xy`` an index lookup instead of a full polygon scan, so
    the port filter does not turn a 5-hour recording into an all-night job.
    """
    if land is None or land.is_empty or min_offshore_km <= 0:
        return None
    buffered = land.buffer(min_offshore_km / KM_PER_DEG)
    if buffered.is_empty:
        return None
    prepare(buffered)  # in-place in shapely 2; used by intersects_xy below
    return buffered


def _ship_type_filter(
    series: Mapping[int, Sequence[Ping]],
    ship_types: ShipTypeFilter | None,
    vessel_types: Mapping[int, int | None] | None,
) -> set[int]:
    """The MMSIs allowed by the optional ship-type filter.

    Pings carry only kinematic data, so static AIS types arrive separately in `vessel_types`.
    A missing filter allows everyone. With a filter, an unknown type is excluded: the caller
    asked for specific classes, and "unknown" is not evidence of being one of them.
    """
    if ship_types is None:
        return set(series)
    types = vessel_types or {}
    if callable(ship_types):
        return {mmsi for mmsi in series if ship_types(types.get(mmsi))}
    allowed = set(ship_types)
    return {mmsi for mmsi in series if types.get(mmsi) is not None and types[mmsi] in allowed}


def _duration_score(minutes: float, min_minutes: float) -> float:
    """Duration component of severity: 0.3 at the threshold, 1.0 at three hours, linear between.

    The issue calibrates this on the Baltic recording: a half-hour rendezvous is worth noting,
    three hours together is not a coincidence, and extra minutes past 3 h add no new evidence.
    """
    span = SEVERITY_FULL_MINUTES - min_minutes
    if span <= 0:
        return 1.0 if minutes > min_minutes else SEVERITY_FLOOR
    fraction = min(1.0, max(0.0, (minutes - min_minutes) / span))
    return SEVERITY_FLOOR + (1.0 - SEVERITY_FLOOR) * fraction


def _finish(
    key: tuple[int, int],
    run: _Run,
    min_minutes: float,
    land: BaseGeometry | None,
) -> Event | None:
    """Turn a finished run into an Event, or None if it was shorter than `min_minutes`.

    Severity now follows the two signals that survived calibration: how long the pair stayed
    together and how far offshore it happened. Duration contributes most of the score; offshore
    distance separates a real rendezvous from a pair loitering just outside a harbour. Without a
    land geometry there is no offshore evidence, so severity is the duration score alone rather
    than a guess about water the caller did not describe.
    """
    minutes = (run.end - run.start).total_seconds() / 60.0
    if minutes + 1e-9 < min_minutes:
        return None
    mean_sog = sum(run.sogs) / len(run.sogs) if run.sogs else 0.0
    twin = run.max_m < TWIN_MAX_M
    duration_score = _duration_score(minutes, min_minutes)
    offshore_km: float | None = None
    if land is None or land.is_empty:
        severity = duration_score
    else:
        # Distance is measured at the closest-approach point: that is where the detector says
        # the meeting happened, and it is the least generous reading of "offshore".
        offshore_km = land.distance(Point(run.lon, run.lat)) * KM_PER_DEG
        offshore_score = min(1.0, offshore_km / OFFSHORE_FULL_KM)
        severity = 0.6 * duration_score + 0.4 * offshore_score

    evidence: dict[str, float | bool] = {
        "min_distance_m": round(run.min_m, 1),
        "max_distance_m": round(run.max_m, 1),
        "minutes": round(minutes, 1),
        "mean_sog": round(mean_sog, 2),
    }
    if offshore_km is not None:
        evidence["offshore_km"] = round(offshore_km, 2)
    if twin:
        evidence["twin_transponders"] = True

    if twin:
        explanation = (
            f"Two MMSIs stayed within {run.max_m:.0f} m of each other for {minutes:.0f} min: "
            f"likely one hull broadcasting two identities."
        )
    else:
        explanation = (
            f"Two vessels stayed within {run.min_m:.0f} m of each other at {mean_sog:.1f} kn "
            f"for {minutes:.0f} min."
        )

    return Event(
        kind="meeting",
        mmsi=key[0],
        other_mmsi=key[1],
        start=run.start,
        end=run.end,
        lat=run.lat,
        lon=run.lon,
        severity=round(min(1.0, max(0.0, severity)), 3),
        explanation=explanation,
        evidence=evidence,
    )


def _scan(
    tracks: Mapping[int, Sequence[Ping]],
    *,
    max_m: float,
    max_sog: float,
    min_minutes: int,
    land: BaseGeometry | None,
    min_offshore_km: float,
    ship_types: ShipTypeFilter | None,
    vessel_types: Mapping[int, int | None] | None,
) -> tuple[list[Event], list[Event]]:
    """Shared pass behind :func:`meetings` and :func:`twin_transponders`.

    Returns ``(meetings, twins)``. Splitting the lists costs nothing extra because the run-level
    distance already records whether the pair was ever more than :data:`TWIN_MAX_M` apart.
    """
    series = _sorted_tracks(tracks)
    allowed = _ship_type_filter(series, ship_types, vessel_types)
    series = {mmsi: pings for mmsi, pings in series.items() if mmsi in allowed}
    if len(series) < 2:
        return [], []

    land_mask = _prepare_land(land, min_offshore_km)
    times = _step_times(series)
    if not times:
        return [], []
    resampled: dict[int, list[Ping | None]] = {}
    for mmsi, pings in series.items():
        stamps = [p.t for p in pings]
        resampled[mmsi] = [_nearest(pings, stamps, t) for t in times]

    runs: dict[tuple[int, int], _Run] = {}
    meetings: list[Event] = []
    twins: list[Event] = []

    def finish(key: tuple[int, int], run: _Run) -> None:
        event = _finish(key, run, min_minutes, land)
        if event is None:
            return
        (twins if run.max_m < TWIN_MAX_M else meetings).append(event)

    for index, t in enumerate(times):
        candidates: list[Ping] = []
        for pings in resampled.values():
            p = pings[index]
            if p is None or p.sog is None or p.sog > max_sog:
                continue
            # Status 5 is "moored": a hull tied to a quay next to another hull is not a
            # rendezvous, and in the Baltic recording these were almost every false positive.
            if p.nav_status == MOORED_NAV_STATUS:
                continue
            candidates.append(p)

        if land_mask is not None and candidates:
            # One vectorised predicate for the whole step rather than a Point per vessel; the
            # prepared buffered land makes the port exclusion cheap on thousands of pings.
            near_land = intersects_xy(
                land_mask,
                [p.lon for p in candidates],
                [p.lat for p in candidates],
            )
            candidates = [p for p, inside in zip(candidates, near_land, strict=True) if not inside]

        buckets: dict[tuple[int, int], list[Ping]] = {}
        for p in candidates:
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
            finish(key, runs.pop(key))

        for key, (dist_m, a, b) in step_pairs.items():
            lat, lon = (a.lat + b.lat) / 2.0, (a.lon + b.lon) / 2.0
            run = runs.get(key)
            if run is None:
                runs[key] = _Run(t, t, dist_m, lat, lon, [a.sog, b.sog], dist_m)
            else:
                run.add(t, dist_m, lat, lon, a.sog, b.sog)

    for key, run in runs.items():
        finish(key, run)

    def order(event: Event) -> tuple[datetime, int, int]:
        return event.start, event.mmsi, event.other_mmsi or 0

    return sorted(meetings, key=order), sorted(twins, key=order)


def meetings(
    tracks: Mapping[int, Sequence[Ping]],
    *,
    max_m: float = 500,
    max_sog: float = 3.0,
    min_minutes: int = 30,
    land: BaseGeometry | None = None,
    min_offshore_km: float = LAND_CLEAR_KM,
    ship_types: ShipTypeFilter | None = None,
    vessel_types: Mapping[int, int | None] | None = None,
) -> list[Event]:
    """Visible meetings: a maximal slow-and-close run of at least `min_minutes`.

    Each track is resampled to 5-minute steps using its nearest ping within 10 minutes. At every
    step vessels are bucketed into ~0.01-degree cells and each cell is compared only with a small
    block of neighbouring cells sized from `max_m`, so cost scales with the number of occupied
    cells rather than with the square of the fleet. A pair qualifies at a step when both have a
    position, both SOGs are known and <= `max_sog`, both are not AIS-moored (status 5), and the
    geodesic separation is <= `max_m`. With a land geometry, either vessel being within
    `min_offshore_km` of the coast skips the step: ports are full of hulls lying side by side.

    A pair that never separates by :data:`TWIN_MAX_M` is one transponder duplicated on one hull,
    not a meeting; those are returned by :func:`twin_transponders` instead. `ship_types` filters
    vessels by AIS type code (a predicate or a collection), using the static types in
    `vessel_types` because pings do not carry them. A missing filter keeps every vessel.

    Returns one Event per meeting, with the lower MMSI in `mmsi`; evidence holds the closest
    approach (metres), the run length (minutes), the mean SOG (knots) and, when land is supplied,
    the distance offshore (km).
    """
    found, _ = _scan(
        tracks,
        max_m=max_m,
        max_sog=max_sog,
        min_minutes=min_minutes,
        land=land,
        min_offshore_km=min_offshore_km,
        ship_types=ship_types,
        vessel_types=vessel_types,
    )
    return found


def twin_transponders(
    tracks: Mapping[int, Sequence[Ping]],
    *,
    max_m: float = 500,
    max_sog: float = 3.0,
    min_minutes: int = 30,
    land: BaseGeometry | None = None,
    min_offshore_km: float = LAND_CLEAR_KM,
    ship_types: ShipTypeFilter | None = None,
    vessel_types: Mapping[int, int | None] | None = None,
) -> list[Event]:
    """Meeting runs that are really one hull broadcasting two MMSIs.

    Takes the same arguments as :func:`meetings` and applies the same port, mooring and type
    filters, so the two functions partition the qualifying runs: a pair whose separation stayed
    below :data:`TWIN_MAX_M` at every step is returned here and never by ``meetings``. Keeping it
    as a separate list lets the snapshot merge or suppress the duplicate identity later without
    losing the evidence that the two MMSIs reported from one place.
    """
    _, twins = _scan(
        tracks,
        max_m=max_m,
        max_sog=max_sog,
        min_minutes=min_minutes,
        land=land,
        min_offshore_km=min_offshore_km,
        ship_types=ship_types,
        vessel_types=vessel_types,
    )
    return twins


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
