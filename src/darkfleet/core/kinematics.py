"""Kinematics rules for the Baltic engine (issue #73): silences, impossible jumps, clones.

Pure functions over the shared `core.tracks` types: no I/O, no recorder assumptions. Every rule
takes its thresholds as arguments so issue #74 can sweep them and report false positives; the
defaults are the frozen values in docs/baltic-spec.md. Each returned Event carries the numbers
that justify it plus one plain-English sentence for the UI, because a finding nobody can read is
not a finding.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from .geo import KNOT_KM_H, distance_km
from .tracks import CoverageMinute, Event, Ping

# Below steerage way a ship is berthed or moored, where hours without a position report are
# normal. This floor applies even when a validation sweep sets min_sog lower, so a stationary
# ship can never be reported as "gone dark".
BERTH_SOG_KN = 0.5

# Local-activity index: coarse cells are enough because the question is whether *any* other
# traffic was being heard nearby, not exactly where each ship was. 0.5 degrees is about 55 km
# north-south, so a 60 km search never has to look at many cells.
ACTIVITY_CELL_DEG = 0.5
_KM_PER_DEG_LAT = 111.32
_DEFAULT_LOCAL_RADIUS_KM = 60.0
_DEFAULT_ACTIVITY_BUCKET_MIN = 15.0
_DEFAULT_MIN_LOCAL_VESSELS = 2

# A clone hop must beat 55 kn, roughly three times a default ship's service cap. The 6 h span
# is the longest a real two-transmitter alternation can plausibly stay one episode; the old
# 10-minute window missed the recorded 630001188 case, whose hops are 5-60 minutes apart.
CLONE_IMPLIED_KN = 55.0
CLONE_WINDOW_MIN = 360.0

# (bucket, lat cell, lon cell) -> every MMSI heard in that place and time slice.
ActivityIndex = dict[tuple[int, int, int], set[int]]


def split_tracks(pings: Iterable[Ping]) -> dict[int, list[Ping]]:
    """Group raw pings into clean, time-sorted tracks per MMSI.

    AIS encodes "position unavailable" as 91/181, and some feeds pad missing fixes with (0, 0).
    Either one, left in, becomes an impossible jump across the globe, so both are dropped here
    before any physics rule looks at them. Exact duplicates appear when overlapping receivers
    ingest the same message; collapsing them keeps duplicate reports from shortening gaps or
    inflating clone flip counts.
    """
    seen: set[Ping] = set()
    by_mmsi: dict[int, list[Ping]] = {}
    for ping in pings:
        if ping.lat == 0.0 and ping.lon == 0.0:
            continue
        if abs(ping.lat) > 90.0 or abs(ping.lon) > 180.0:
            continue
        if ping in seen:
            continue
        seen.add(ping)
        by_mmsi.setdefault(ping.mmsi, []).append(ping)
    for track in by_mmsi.values():
        track.sort(key=lambda p: p.t)
    return by_mmsi


def _minute_counts(coverage: Iterable[CoverageMinute]) -> dict[datetime, int]:
    """Messages per UTC minute, summing duplicate minute rows rather than dropping one."""
    counts: dict[datetime, int] = {}
    for row in coverage:
        minute = row.minute.replace(second=0, microsecond=0)
        counts[minute] = counts.get(minute, 0) + row.n_msgs
    return counts


def covered(
    t0: datetime, t1: datetime, coverage: Iterable[CoverageMinute], min_msgs_per_min: int = 150
) -> bool:
    """True only if the recorder heard enough ships in every minute of [t0, t1].

    A minute absent from `coverage` means zero messages: the receiver was down, not the ship
    silent. Both end minutes are included, including partial ones, which is the conservative
    choice: a partial minute can only suppress a silence, never invent one.
    """
    if t1 < t0:
        return False
    counts = _minute_counts(coverage)
    minute = t0.replace(second=0, microsecond=0)
    end = t1.replace(second=0, microsecond=0)
    while minute <= end:
        if counts.get(minute, 0) < min_msgs_per_min:
            return False
        minute += timedelta(minutes=1)
    return True


def _coverage_floor(t0: datetime, t1: datetime, coverage: Iterable[CoverageMinute]) -> int:
    """Fewest messages heard in any minute of [t0, t1], for event evidence."""
    counts = _minute_counts(coverage)
    minute = t0.replace(second=0, microsecond=0)
    end = t1.replace(second=0, microsecond=0)
    floor: int | None = None
    while minute <= end:
        n = counts.get(minute, 0)
        floor = n if floor is None else min(floor, n)
        minute += timedelta(minutes=1)
    return 0 if floor is None else floor


def _fmt_duration(minutes: float) -> str:
    """Human duration such as '2 h 14 min', matching how the UI talks about gaps."""
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def _activity_bucket(t: datetime, bucket_min: float) -> int:
    """Floor a time to a fixed-width bucket counted from the Unix epoch.

    Counting from the epoch rather than from the first ping keeps every caller's buckets
    aligned, so one index built once can answer gap queries for every vessel in the recording.
    """
    return int(t.timestamp() // (bucket_min * 60.0))


def _lat_cell(lat: float) -> int:
    return math.floor(lat / ACTIVITY_CELL_DEG)


def _lon_cell(lon: float) -> int:
    """Longitude cell, normalised to [-180, 180) so antimeridian fixes share one key."""
    wrapped = (lon + 180.0) % 360.0 - 180.0
    return math.floor(wrapped / ACTIVITY_CELL_DEG)


def _cell_distance_km(lat: float, lon: float, lat_cell: int, lon_cell: int) -> float:
    """Shortest distance from a point to a cell's bounding box (zero when inside it)."""
    lat0 = lat_cell * ACTIVITY_CELL_DEG
    lon0 = lon_cell * ACTIVITY_CELL_DEG
    near_lat = min(max(lat, lat0), lat0 + ACTIVITY_CELL_DEG)
    near_lon = min(max(lon, lon0), lon0 + ACTIVITY_CELL_DEG)
    return distance_km(lat, lon, near_lat, near_lon)


def build_activity_index(
    tracks: Mapping[int, Sequence[Ping]], *, bucket_min: float = _DEFAULT_ACTIVITY_BUCKET_MIN
) -> ActivityIndex:
    """Build one (bucket, cell) -> MMSI index for every silence query in a run.

    A busy recording has thousands of tracks; rebuilding a spatial index for each gap would be
    quadratic. The index stores identities rather than exact positions because local activity
    only asks how many *other* ships were being heard nearby, and the cell is deliberately
    coarse so a whole recording fits in memory.
    """
    index: ActivityIndex = {}
    for mmsi, track in tracks.items():
        for ping in track:
            key = (
                _activity_bucket(ping.t, bucket_min),
                _lat_cell(ping.lat),
                _lon_cell(ping.lon),
            )
            index.setdefault(key, set()).add(mmsi)
    return index


def _nearby_vessel_counts(
    pings_by_time_index: ActivityIndex,
    lat: float,
    lon: float,
    t0: datetime,
    t1: datetime,
    *,
    radius_km: float,
    bucket_min: float,
    exclude_mmsi: int | None,
) -> list[int]:
    """Distinct other MMSI heard near a point in each bucket of [t0, t1].

    Cells whose bounding box lies beyond `radius_km` are skipped before any lookup. A cell is
    treated as a whole, which can only over-count near a cell edge; that is the safe direction
    for suppressing a silence, because the alternative is blaming a ship for a gap the
    receiver network could already explain.
    """
    if t1 < t0:
        return []
    lat_span = math.ceil((radius_km / _KM_PER_DEG_LAT) / ACTIVITY_CELL_DEG)
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    lon_span = math.ceil((radius_km / (_KM_PER_DEG_LAT * cos_lat)) / ACTIVITY_CELL_DEG)
    base_lat = _lat_cell(lat)
    base_lon = _lon_cell(lon)
    lon_cells = int(360.0 / ACTIVITY_CELL_DEG)
    half_lon_cells = lon_cells // 2
    candidate_cells: list[tuple[int, int]] = []
    for dlat in range(-lat_span, lat_span + 1):
        for dlon in range(-lon_span, lon_span + 1):
            cell_lat = base_lat + dlat
            cell_lon = ((base_lon + dlon + half_lon_cells) % lon_cells) - half_lon_cells
            if _cell_distance_km(lat, lon, cell_lat, cell_lon) <= radius_km:
                candidate_cells.append((cell_lat, cell_lon))
    counts: list[int] = []
    first_bucket = _activity_bucket(t0, bucket_min)
    last_bucket = _activity_bucket(t1, bucket_min)
    for bucket in range(first_bucket, last_bucket + 1):
        heard: set[int] = set()
        for cell_lat, cell_lon in candidate_cells:
            heard.update(pings_by_time_index.get((bucket, cell_lat, cell_lon), ()))
        if exclude_mmsi is not None:
            heard.discard(exclude_mmsi)
        counts.append(len(heard))
    return counts


def local_activity(
    pings_by_time_index: ActivityIndex,
    lat: float,
    lon: float,
    t0: datetime,
    t1: datetime,
    *,
    radius_km: float = _DEFAULT_LOCAL_RADIUS_KM,
    bucket_min: float = _DEFAULT_ACTIVITY_BUCKET_MIN,
    min_vessels: int = _DEFAULT_MIN_LOCAL_VESSELS,
    exclude_mmsi: int | None = None,
) -> bool:
    """True when receivers near the last fix kept hearing enough *other* ships.

    Global coverage says the receiver network was alive somewhere in the box; it does not say
    this ship was inside anyone's range. Asking locally fixes that: a ship that leaves one
    receiver's footprint while receivers elsewhere stay busy is no longer called dark merely
    because the whole feed stayed busy.
    """
    counts = _nearby_vessel_counts(
        pings_by_time_index,
        lat,
        lon,
        t0,
        t1,
        radius_km=radius_km,
        bucket_min=bucket_min,
        exclude_mmsi=exclude_mmsi,
    )
    return bool(counts) and all(n >= min_vessels for n in counts)


def silences(
    track: Sequence[Ping],
    coverage: Iterable[CoverageMinute],
    *,
    min_gap_min: float = 60.0,
    min_sog: float = 2.0,
    activity: ActivityIndex | None = None,
) -> list[Event]:
    """AIS gaps that belong to the ship, not to our receiver network.

    A gap only counts while the ship was last seen underway (SOG at or above `min_sog`, and never
    below the berth floor) and only if `covered` proves the recorder was hearing the rest of the
    traffic for the whole gap. When `activity` is supplied, `local_activity` must also hold:
    receivers within 60 km of the last fix must have been hearing other ships throughout. Global
    coverage alone cannot tell a ship leaving one receiver's range from a ship that switched off
    while receivers elsewhere stayed busy. Severity rises with duration and with the distance
    covered while dark, because both make the gap harder to explain as a missed message.
    """
    events: list[Event] = []
    sog_floor = max(min_sog, BERTH_SOG_KN)
    for last, nxt in pairwise(sorted(track, key=lambda p: p.t)):
        minutes = (nxt.t - last.t).total_seconds() / 60.0
        if minutes <= min_gap_min:
            continue
        if last.sog is None or last.sog < sog_floor:
            continue
        if not covered(last.t, nxt.t, coverage):
            continue
        local_heard: int | None = None
        if activity is not None:
            if not local_activity(activity, last.lat, last.lon, last.t, nxt.t, exclude_mmsi=last.mmsi):
                continue
            local_heard = min(
                _nearby_vessel_counts(
                    activity,
                    last.lat,
                    last.lon,
                    last.t,
                    nxt.t,
                    radius_km=_DEFAULT_LOCAL_RADIUS_KM,
                    bucket_min=_DEFAULT_ACTIVITY_BUCKET_MIN,
                    exclude_mmsi=last.mmsi,
                )
            )
        km = distance_km(last.lat, last.lon, nxt.lat, nxt.lon)
        implied_kn = km / (minutes / 60.0) / KNOT_KM_H
        duration_score = min(1.0, minutes / (4.0 * max(min_gap_min, 1.0)))
        distance_score = min(1.0, km / 200.0)
        severity = round(min(1.0, 0.5 * duration_score + 0.5 * distance_score), 3)
        if local_heard is None:
            explanation = (
                f"Silent for {_fmt_duration(minutes)} while underway at {last.sog:.0f} kn; "
                "the receiver network was hearing other ships the whole time."
            )
        else:
            explanation = (
                f"Silent for {_fmt_duration(minutes)} while underway at {last.sog:.0f} kn; "
                f"receivers within {_DEFAULT_LOCAL_RADIUS_KM:.0f} km kept hearing "
                f"{local_heard} other ships the whole time."
            )
        explanation = explanation.rstrip(".")
        explanation += f"; it reappeared {km:.1f} km from where it went quiet"
        if km < 2.0:
            # A ship that reappears almost exactly where it went quiet may just have stopped
            # reporting while berthed; that is weaker evidence of a deliberate switch-off.
            severity = round(severity * 0.5, 3)
            explanation += " (it may simply have stopped)"
        explanation += "."
        evidence: dict[str, Any] = {
            "minutes": round(minutes, 1),
            "last_lat": last.lat,
            "last_lon": last.lon,
            "next_lat": nxt.lat,
            "next_lon": nxt.lon,
            "distance_km": round(km, 1),
            "moved_km": round(km, 1),
            "implied_kn": round(implied_kn, 1),
            "coverage_min_msgs": _coverage_floor(last.t, nxt.t, coverage),
        }
        if local_heard is not None:
            evidence["local_other_vessels"] = local_heard
        events.append(
            Event(
                kind="silence",
                mmsi=last.mmsi,
                start=last.t,
                end=nxt.t,
                # Mark the map where the ship went dark, not where it reappeared.
                lat=last.lat,
                lon=last.lon,
                severity=severity,
                explanation=explanation,
                evidence=evidence,
            )
        )
    return events


def jumps(track: Sequence[Ping], cap_kn: float, *, min_km: float = 5.0, factor: float = 1.5) -> list[Event]:
    """Consecutive pings that require more than `factor` times the class speed cap.

    `cap_kn` comes from core.speeds.max_speed_knots(class), passed by the caller so this module
    stays independent of ship-type mapping. A small `min_km` floor ignores the GPS jitter of a
    ship tied up at the quay, where a few metres in a second can look arbitrarily fast.
    """
    events: list[Event] = []
    limit_kn = factor * cap_kn
    for last, nxt in pairwise(sorted(track, key=lambda p: p.t)):
        seconds = (nxt.t - last.t).total_seconds()
        if seconds <= 0:
            # No interval means no implied speed; clones handle same-time identity conflicts.
            continue
        km = distance_km(last.lat, last.lon, nxt.lat, nxt.lon)
        if km <= min_km:
            continue
        implied_kn = km / (seconds / 3600.0) / KNOT_KM_H
        if implied_kn <= limit_kn:
            continue
        ratio = implied_kn / cap_kn if cap_kn > 0 else float("inf")
        events.append(
            Event(
                kind="jump",
                mmsi=last.mmsi,
                start=last.t,
                end=nxt.t,
                # The impossible fix is the one worth showing on the map.
                lat=nxt.lat,
                lon=nxt.lon,
                severity=round(min(1.0, ratio / 3.0), 3),
                explanation=(
                    f"Jumped {km:.0f} km in {seconds / 60.0:.0f} min ({implied_kn:.0f} kn), "
                    f"{ratio:.1f} times the {cap_kn:.0f} kn cap for this ship class; "
                    "AIS cannot move that fast."
                ),
                evidence={
                    "km": round(km, 1),
                    "minutes": round(seconds / 60.0, 1),
                    "implied_kn": round(implied_kn, 1),
                    "cap_kn": round(cap_kn, 2),
                    "ratio": round(ratio, 2),
                    "factor": factor,
                    # Both ends, so a ghost-point filter can drop a jump that leaves a false fix
                    # as well as one that arrives at it (`lat`/`lon` only holds the arrival).
                    "last_lat": last.lat,
                    "last_lon": last.lon,
                    "next_lat": nxt.lat,
                    "next_lon": nxt.lon,
                },
            )
        )
    return events


def _cluster_labels(points: Sequence[Ping], min_km: float) -> list[int] | None:
    """Label every fix A (0) or B (1), or None when no fix is far enough to open B.

    The first fix defines A. A fix more than `min_km` from it opens B at that fix, and every
    later fix joins whichever cluster centre is nearer. This is deliberately the simplest
    two-mean split: real clone pairs sit tens of kilometres apart, so a heavier clustering
    method would add cost without changing the answer.
    """
    if not points:
        return None
    first = points[0]
    a_sum = [first.lat, first.lon, 1.0]
    b_sum: list[float] | None = None
    labels = [0]
    for ping in points[1:]:
        if b_sum is None:
            if distance_km(ping.lat, ping.lon, first.lat, first.lon) > min_km:
                b_sum = [ping.lat, ping.lon, 1.0]
                labels.append(1)
            else:
                a_sum[0] += ping.lat
                a_sum[1] += ping.lon
                a_sum[2] += 1.0
                labels.append(0)
            continue
        a_lat = a_sum[0] / a_sum[2]
        a_lon = a_sum[1] / a_sum[2]
        b_lat = b_sum[0] / b_sum[2]
        b_lon = b_sum[1] / b_sum[2]
        if distance_km(ping.lat, ping.lon, a_lat, a_lon) <= distance_km(ping.lat, ping.lon, b_lat, b_lon):
            labels.append(0)
            a_sum[0] += ping.lat
            a_sum[1] += ping.lon
            a_sum[2] += 1.0
        else:
            labels.append(1)
            b_sum[0] += ping.lat
            b_sum[1] += ping.lon
            b_sum[2] += 1.0
    return labels if b_sum is not None else None


def _clone_flips(points: Sequence[Ping], labels: Sequence[int]) -> list[tuple[int, float]]:
    """A<->B transitions whose implied speed is faster than any ship can travel.

    A slow ferry crossing between the two places is not a clone; only a hop that beats the
    threefold speed margin counts. Same-time fixes carry no implied speed and are left alone.
    """
    flips: list[tuple[int, float]] = []
    for i in range(len(points) - 1):
        if labels[i] == labels[i + 1]:
            continue
        seconds = (points[i + 1].t - points[i].t).total_seconds()
        if seconds <= 0:
            continue
        km = distance_km(points[i].lat, points[i].lon, points[i + 1].lat, points[i + 1].lon)
        implied_kn = km / (seconds / 3600.0) / KNOT_KM_H
        if implied_kn > CLONE_IMPLIED_KN:
            flips.append((i, implied_kn))
    return flips


def _flip_groups(
    flips: Sequence[tuple[int, float]], points: Sequence[Ping], *, window_min: float, min_flips: int
) -> list[tuple[int, int]]:
    """Index ranges over `flips` holding at least `min_flips` inside one `window_min` span.

    Windows that share a fix are one continuous episode; a quiet gap longer than the window
    starts a new one. Grouping by point range rather than flip index keeps two episodes hours
    apart from being glued together just because no non-flip transition sat between them.
    """
    if len(flips) < min_flips:
        return []
    span_s = window_min * 60.0
    candidates: list[tuple[int, int]] = []
    end = 0
    for start in range(len(flips)):
        if end < start:
            end = start
        start_t = points[flips[start][0] + 1].t
        while end + 1 < len(flips):
            next_t = points[flips[end + 1][0] + 1].t
            if (next_t - start_t).total_seconds() > span_s:
                break
            end += 1
        if end - start + 1 >= min_flips:
            candidates.append((start, end))
    groups: list[tuple[int, int]] = []
    for start, finish in candidates:
        first_point = flips[start][0]
        if groups:
            previous_first, previous_finish = groups[-1]
            if first_point <= flips[previous_finish][0] + 1:
                groups[-1] = (previous_first, max(previous_finish, finish))
                continue
        groups.append((start, finish))
    return groups


def _clone_event(
    points: Sequence[Ping],
    labels: Sequence[int],
    flips: Sequence[tuple[int, float]],
    start: int,
    finish: int,
    min_flips: int,
) -> Event:
    """Turn one qualifying flip range into the single Event the UI shows for that episode."""
    first_point = flips[start][0]
    last_point = flips[finish][0] + 1
    window = points[first_point : last_point + 1]
    window_labels = labels[first_point : last_point + 1]
    cluster_a = [ping for i, ping in enumerate(window) if window_labels[i] == 0]
    cluster_b = [ping for i, ping in enumerate(window) if window_labels[i] == 1]
    a_lat = sum(p.lat for p in cluster_a) / len(cluster_a)
    a_lon = sum(p.lon for p in cluster_a) / len(cluster_a)
    b_lat = sum(p.lat for p in cluster_b) / len(cluster_b)
    b_lon = sum(p.lon for p in cluster_b) / len(cluster_b)
    separation = distance_km(a_lat, a_lon, b_lat, b_lon)
    flip_count = finish - start + 1
    max_implied = max(flips[i][1] for i in range(start, finish + 1))
    minutes = (window[-1].t - window[0].t).total_seconds() / 60.0
    return Event(
        kind="clone",
        mmsi=window[0].mmsi,
        start=window[0].t,
        end=window[-1].t,
        lat=window[0].lat,
        lon=window[0].lon,
        severity=round(min(1.0, flip_count / (2.0 * max(min_flips, 1))), 3),
        explanation=(
            f"One identity reported from two places {separation:.0f} km apart, "
            f"switching {flip_count} times in {_fmt_duration(minutes)}; one ship cannot do that, "
            "so two transmitters share this ID (or a device is misconfigured)."
        ),
        evidence={
            "cluster_a": [round(a_lat, 5), round(a_lon, 5)],
            "cluster_b": [round(b_lat, 5), round(b_lon, 5)],
            "separation_km": round(separation, 1),
            "flips": flip_count,
            "max_implied_kn": round(max_implied, 1),
            "first": window[0].t.isoformat(),
            "last": window[-1].t.isoformat(),
        },
    )


def clones(
    track: Sequence[Ping],
    *,
    window_min: float = 10.0,
    min_km: float = 30.0,
    min_flips: int = 3,
) -> list[Event]:
    """Episodes where one MMSI alternates between two places faster than a ship can travel.

    Two transmitters sharing an identity show up as two position clusters visited in turn. We
    label each fix A or B once, keep only A<->B transitions whose implied speed beats 55 kn,
    and report every 6 h span holding at least `min_flips` of them. `window_min` is accepted
    and ignored for compatibility: the old 10-minute window missed the recorded 630001188
    case, whose hops are 5-60 minutes apart.
    """
    points = sorted(track, key=lambda p: p.t)
    labels = _cluster_labels(points, min_km)
    if labels is None:
        return []
    flips = _clone_flips(points, labels)
    events: list[Event] = []
    for start, finish in _flip_groups(flips, points, window_min=CLONE_WINDOW_MIN, min_flips=min_flips):
        events.append(_clone_event(points, labels, flips, start, finish, min_flips))
    return events
