"""Hormuz story rules: which AIS gaps are one outage, and how far a gap could have reached.

Pure functions only (no I/O): the build script fetches GFW gaps and writes the snapshot; this
module decides what the gaps mean. A cluster is a group of ships that went silent at the same
time and place. That pattern is a reception or jamming outage, so it is never ranked as ships
hiding; a single gap is a candidate only when it is not part of a cluster.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from darkfleet.core.geo import distance_km
from darkfleet.core.tracks import Ping
from darkfleet.core.watch import effective_cap_kn

# An outage cluster: gaps starting within this many hours and off-positions this close.
CLUSTER_WINDOW_HOURS = 3.0
CLUSTER_RADIUS_KM = 60.0
CLUSTER_MIN_VESSELS = 8


# A listing counts as a risk only when the list says something about the ship itself. Port
# inspection records (e.g. the Tokyo MOU PSC dataset) tag a ship with no risk at all, so a
# ship whose only evidence is one is not a story. ``mare.detained`` is a port-state outcome and
# is not enough on its own either.
RISK_TAGS = frozenset({"sanction", "mare.shadow", "poi", "reg.warn"})


def is_risk_tagged(tags: Iterable[str]) -> bool:
    """True when the list tags the ship with at least one risk tag (see ``RISK_TAGS``)."""
    return any(tag in RISK_TAGS for tag in tags)


def imo_agrees(gap_imo: str | None, listed_imo: str | None) -> bool:
    """MMSI matched; now check the IMO number where both sides have one.

    An MMSI can be reused by another hull, so two different IMO numbers mean a different ship.
    A missing IMO on either side proves nothing and does not reject the match.
    """
    if not gap_imo or not listed_imo:
        return True
    return gap_imo.strip() == listed_imo.strip()


@dataclass(frozen=True, slots=True)
class Gap:
    """One AIS silence: where the ship went dark and where it came back."""

    id: str
    vessel_key: str  # MMSI when GFW gives one, else GFW's vessel id: one ship, one key
    start: datetime
    end: datetime
    off_lat: float
    off_lon: float
    on_lat: float
    on_lon: float
    # GFW's implied speed over the gap (knots), and the class cap for the ship's type.
    implied_kn: float | None = None
    class_cap_kn: float = 14.0


def _anchor(gap: Gap, anchor: Literal["off", "on"]) -> tuple[float, float]:
    return (gap.off_lat, gap.off_lon) if anchor == "off" else (gap.on_lat, gap.on_lon)


def _links(a: Gap, b: Gap, window_hours: float, radius_km: float, anchor: Literal["off", "on"]) -> bool:
    gap_hours = abs((a.start - b.start).total_seconds()) / 3600.0
    if gap_hours > window_hours:
        return False
    return distance_km(*_anchor(a, anchor), *_anchor(b, anchor)) <= radius_km


def cluster_gaps(
    gaps: Iterable[Gap],
    *,
    window_hours: float = CLUSTER_WINDOW_HOURS,
    radius_km: float = CLUSTER_RADIUS_KM,
    min_vessels: int = CLUSTER_MIN_VESSELS,
    anchor: Literal["off", "on"] = "off",
) -> list[tuple[Gap, ...]]:
    """Connected groups of gaps that share a start window and a place.

    Two gaps are linked when their starts are within `window_hours` and their `anchor` positions
    (where the ship went dark, or where it came back) are within `radius_km`. A group is kept
    as an outage cluster only when it covers at least `min_vessels` distinct ships. Groups are
    returned earliest first; gaps inside a group are in start order. The result does not depend
    on the input order.
    """
    items = sorted(gaps, key=lambda g: (g.start, g.id))
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[j].start - items[i].start > timedelta(hours=window_hours):
                break  # sorted by start: nothing later can link to i
            if _links(items[i], items[j], window_hours, radius_km, anchor):
                parent[find(j)] = find(i)

    groups: dict[int, list[Gap]] = {}
    for i, gap in enumerate(items):
        groups.setdefault(find(i), []).append(gap)
    clusters = [
        tuple(members)
        for members in groups.values()
        if len({gap.vessel_key for gap in members}) >= min_vessels
    ]
    return sorted(clusters, key=lambda members: (members[0].start, members[0].id))


def reach_radius_km(cap_kn: float, gap: Gap) -> float:
    """How far a ship capped at `cap_kn` could have travelled while the gap lasted."""
    hours = (gap.end - gap.start).total_seconds() / 3600.0
    return max(0.0, cap_kn * 1.852 * hours)


def cluster_summary(members: Sequence[Gap]) -> dict[str, float | int | None]:
    """Counts, spans and speeds a card needs. Pure arithmetic over the members.

    `median_kn`/`min_kn` are the implied speeds from the reported off and on positions. A ship
    whose implied speed is above its class cap raised the cap; `raised_cap` counts those, using
    the core `effective_cap_kn` (a ship whose own speed proves faster than its class cap gets the
    higher cap, so it is not an outlier by declaration alone).
    """
    starts = [gap.start for gap in members]
    first, last = min(starts), max(starts)
    speeds = [gap.implied_kn for gap in members if gap.implied_kn is not None]
    raised = 0
    for gap in members:
        if gap.implied_kn is None:
            continue
        track = [
            Ping(mmsi=0, t=gap.start, lat=gap.off_lat, lon=gap.off_lon, sog=gap.implied_kn),
            Ping(mmsi=0, t=gap.end, lat=gap.on_lat, lon=gap.on_lon, sog=gap.implied_kn),
        ]
        if effective_cap_kn(gap.class_cap_kn, track) > gap.class_cap_kn:
            raised += 1
    return {
        "vessels": len({gap.vessel_key for gap in members}),
        "gaps": len(members),
        "span_hours": round((last - first).total_seconds() / 3600.0, 2),
        "median_hours": round(median((g.end - g.start).total_seconds() / 3600.0 for g in members), 2),
        "max_km": round(max(distance_km(g.off_lat, g.off_lon, g.on_lat, g.on_lon) for g in members), 1),
        "median_kn": round(median(speeds), 1) if speeds else None,
        "min_kn": round(min(speeds), 1) if speeds else None,
        "raised_cap": raised,
    }
