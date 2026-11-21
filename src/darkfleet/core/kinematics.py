"""Kinematics rules for the Baltic engine (issue #73): silences, impossible jumps, clones.

Pure functions over the shared `core.tracks` types: no I/O, no recorder assumptions. Every rule
takes its thresholds as arguments so issue #74 can sweep them and report false positives; the
defaults are the frozen values in docs/baltic-spec.md. Each returned Event carries the numbers
that justify it plus one plain-English sentence for the UI, because a finding nobody can read is
not a finding.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from itertools import combinations, pairwise

from .geo import KNOT_KM_H, distance_km
from .tracks import CoverageMinute, Event, Ping

# Below steerage way a ship is berthed or moored, where hours without a position report are
# normal. This floor applies even when a validation sweep sets min_sog lower, so a stationary
# ship can never be reported as "gone dark".
BERTH_SOG_KN = 0.5


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


def silences(
    track: Sequence[Ping],
    coverage: Iterable[CoverageMinute],
    *,
    min_gap_min: float = 60.0,
    min_sog: float = 2.0,
) -> list[Event]:
    """AIS gaps that belong to the ship, not to our receiver network.

    A gap only counts while the ship was last seen underway (SOG at or above `min_sog`, and never
    below the berth floor) and only if `covered` proves the recorder was hearing the rest of the
    traffic for the whole gap. Severity rises with duration and with the distance covered while
    dark, because both make the gap harder to explain as a missed message.
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
        km = distance_km(last.lat, last.lon, nxt.lat, nxt.lon)
        implied_kn = km / (minutes / 60.0) / KNOT_KM_H
        duration_score = min(1.0, minutes / (4.0 * max(min_gap_min, 1.0)))
        distance_score = min(1.0, km / 200.0)
        severity = round(min(1.0, 0.5 * duration_score + 0.5 * distance_score), 3)
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
                explanation=(
                    f"Silent for {_fmt_duration(minutes)} while underway at {last.sog:.0f} kn; "
                    "the receiver network was hearing other ships the whole time."
                ),
                evidence={
                    "minutes": round(minutes, 1),
                    "last_lat": last.lat,
                    "last_lon": last.lon,
                    "next_lat": nxt.lat,
                    "next_lon": nxt.lon,
                    "distance_km": round(km, 1),
                    "implied_kn": round(implied_kn, 1),
                    "coverage_min_msgs": _coverage_floor(last.t, nxt.t, coverage),
                },
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
                },
            )
        )
    return events


def _centroid(points: Sequence[Ping]) -> tuple[float, float]:
    return (
        sum(p.lat for p in points) / len(points),
        sum(p.lon for p in points) / len(points),
    )


def _clone_groups(chain: Sequence[Ping], window_min: float, min_flips: int) -> list[tuple[int, int]]:
    """Disjoint index ranges, each containing at least one qualifying alternation window.

    A qualifying window can start at any ping and holds as many consecutive far-apart pings as
    fit in `window_min`. Windows that share pings are one continuous clone episode; windows that
    do not are separate episodes, even if they belong to the same far-ping chain.
    """
    span_s = window_min * 60.0
    end = 0
    candidates: list[tuple[int, int]] = []
    for start in range(len(chain)):
        if end < start:
            end = start
        while end + 1 < len(chain) and (chain[end + 1].t - chain[start].t).total_seconds() <= span_s:
            end += 1
        if end - start >= min_flips:
            candidates.append((start, end))
    groups: list[tuple[int, int]] = []
    for start, finish in candidates:
        if groups and start <= groups[-1][1]:
            groups[-1] = (groups[-1][0], max(groups[-1][1], finish))
        else:
            groups.append((start, finish))
    return groups


def _best_window(
    chain: Sequence[Ping], start: int, finish: int, window_min: float, min_flips: int
) -> tuple[int, int] | None:
    """The window with the most flips inside one episode, so evidence is the strongest run."""
    span_s = window_min * 60.0
    best: tuple[int, int] | None = None
    best_flips = 0
    end = start
    for left in range(start, finish + 1):
        if end < left:
            end = left
        while end + 1 <= finish and (chain[end + 1].t - chain[left].t).total_seconds() <= span_s:
            end += 1
        flips = end - left
        if flips >= min_flips and flips > best_flips:
            best, best_flips = (left, end), flips
    return best


def _clone_event(window: Sequence[Ping], min_km: float, min_flips: int) -> Event | None:
    """Turn one alternating run into an Event, or None if it is not two tight clusters."""
    cluster_a = window[0::2]
    cluster_b = window[1::2]
    if len(cluster_a) < 2 or len(cluster_b) < 2:
        return None
    a_lat, a_lon = _centroid(cluster_a)
    b_lat, b_lon = _centroid(cluster_b)
    if distance_km(a_lat, a_lon, b_lat, b_lon) <= min_km:
        return None
    spread = max(
        distance_km(p.lat, p.lon, q.lat, q.lon)
        for group in (cluster_a, cluster_b)
        for p, q in combinations(group, 2)
    )
    # Each cluster must stay tight; three unrelated positions are not two transmitters.
    if spread > min_km:
        return None
    separation = max(distance_km(p.lat, p.lon, q.lat, q.lon) for p in cluster_a for q in cluster_b)
    flips = len(window) - 1
    minutes = (window[-1].t - window[0].t).total_seconds() / 60.0
    return Event(
        kind="clone",
        mmsi=window[0].mmsi,
        start=window[0].t,
        end=window[-1].t,
        lat=window[0].lat,
        lon=window[0].lon,
        severity=round(min(1.0, flips / (2.0 * max(min_flips, 1))), 3),
        explanation=(
            f"MMSI {window[0].mmsi} alternated {flips} times between two positions "
            f"{separation:.0f} km apart within {_fmt_duration(minutes)}; "
            "two transmitters are sharing the identity."
        ),
        evidence={
            "flips": flips,
            "minutes": round(minutes, 1),
            "max_separation_km": round(separation, 1),
            "cluster_a_lat": round(a_lat, 5),
            "cluster_a_lon": round(a_lon, 5),
            "cluster_b_lat": round(b_lat, 5),
            "cluster_b_lon": round(b_lon, 5),
            "cluster_a_pings": len(cluster_a),
            "cluster_b_pings": len(cluster_b),
        },
    )


def clones(
    track: Sequence[Ping],
    *,
    window_min: float = 10.0,
    min_km: float = 30.0,
    min_flips: int = 3,
) -> list[Event]:
    """Episodes where one MMSI alternates between two far-apart positions.

    Two transmitters sharing an identity produce a ping-pong: every consecutive pair is farther
    apart than `min_km`, and a short window holds at least `min_flips` such hops. Chains of far
    pings are split into episodes by overlapping qualifying windows, so one physical episode
    yields one Event even when it lasts longer than the window.
    """
    points = sorted(track, key=lambda p: p.t)
    events: list[Event] = []
    i = 0
    while i + 1 < len(points):
        if distance_km(points[i].lat, points[i].lon, points[i + 1].lat, points[i + 1].lon) <= min_km:
            i += 1
            continue
        j = i
        while j + 1 < len(points) and (
            distance_km(points[j].lat, points[j].lon, points[j + 1].lat, points[j + 1].lon) > min_km
        ):
            j += 1
        chain = points[i : j + 1]
        for start, finish in _clone_groups(chain, window_min, min_flips):
            best = _best_window(chain, start, finish, window_min, min_flips)
            if best is None:
                continue
            event = _clone_event(chain[best[0] : best[1] + 1], min_km, min_flips)
            if event is not None:
                events.append(event)
        i = j + 1
    return events
