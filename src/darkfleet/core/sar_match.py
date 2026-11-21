"""Match radar detections to AIS positions at the instant the satellite passed (pure, no I/O).

Radar sees a ship; AIS says who it is. A detection is "matched" when some vessel's AIS position, at
the scene time, is within ``radius_km`` of it. A vessel's position at the scene time is interpolated
between the fixes on either side of it (each at most ``tolerance_s`` away); with a fix on one side
only, the nearest fix inside the tolerance is used as is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .geo import distance_km_array

TOLERANCE_S = 15 * 60
RADIUS_KM = 1.5


@dataclass(frozen=True)
class AisFix:
    t: datetime
    lat: float
    lon: float


def position_at(
    fixes: Sequence[AisFix], when: datetime, tolerance_s: float = TOLERANCE_S
) -> tuple[float, float] | None:
    """Where a vessel was at ``when`` (fixes sorted by time), or None if there is no usable fix."""
    before = after = None
    for fix in fixes:
        dt = (fix.t - when).total_seconds()
        if dt <= 0 and dt >= -tolerance_s:
            before = fix
        elif dt > 0 and dt <= tolerance_s:
            after = fix
            break
    if before and after:
        span = (after.t - before.t).total_seconds()
        f = (when - before.t).total_seconds() / span if span > 0 else 0.0
        lon_gap = after.lon - before.lon
        if abs(lon_gap) > 180:  # across the antimeridian
            lon_gap -= 360.0 * np.sign(lon_gap)
        lon = before.lon + f * lon_gap
        return before.lat + f * (after.lat - before.lat), ((lon + 180.0) % 360.0) - 180.0
    fix = before or after
    return (fix.lat, fix.lon) if fix else None


def match_to_ais(
    detections: Sequence[tuple[float, float]],
    tracks: Mapping[int, Sequence[AisFix]],
    when: datetime,
    *,
    tolerance_s: float = TOLERANCE_S,
    radius_km: float = RADIUS_KM,
) -> list[int | None]:
    """For each ``(lat, lon)`` detection, the MMSI of the nearest vessel within the radius, or None.

    One vessel explains at most one detection (closest pair first), so a ship cannot hide two radar
    blobs by being nearby.
    """
    positions = {m: p for m, f in tracks.items() if (p := position_at(f, when, tolerance_s))}
    result: list[int | None] = [None] * len(detections)
    if not positions or not detections:
        return result
    mmsis = list(positions)
    v_lat = np.array([positions[m][0] for m in mmsis])
    v_lon = np.array([positions[m][1] for m in mmsis])
    d_lat = np.array([d[0] for d in detections])
    d_lon = np.array([d[1] for d in detections])
    dist = distance_km_array(d_lat[:, None], d_lon[:, None], v_lat[None, :], v_lon[None, :])
    pairs = sorted(zip(*np.nonzero(dist <= radius_km), strict=True), key=lambda ij: dist[ij])
    used: set[int] = set()
    for i, j in pairs:
        if result[i] is None and j not in used:
            result[i] = mmsis[j]
            used.add(j)
    return result
