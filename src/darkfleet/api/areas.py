"""Area dashboards (issue #186): one compact stats row per watch area.

The watch screen already knows which boxes matter (``core.areas`` -> ``watch.json``). This
router answers the strip above that map: how many ships are in each box now, what was raised
there in the last twelve hours, how much traffic the recorder is actually hearing, and the
last twelve hourly ship counts for a sparkline.

Everything is file-backed and read-only, like the rest of ``api/``:

* the area list, ships and alerts come from the current ``watch.json`` through
  :func:`darkfleet.api.watch.get_watch`, so the strip can never disagree with the map;
* ``msgs_per_min`` and ``sparkline`` are derived from the recorder's own ``*.jsonl.gz`` hours
  under ``DARKFLEET_AIS_DIR`` (default ``data/cache/ais``), with the one existing parser
  (:func:`darkfleet.ingest.ais_tracks.iter_files`). The recorder's five-minute log lines are
  deliberately *not* read: they are stdout, not data, and disappear when the process restarts.

A watched box borrows its parent listening box's recording: the recorder subscribes to wide
feeds, while the watched areas are the narrow places inside them. Per-hour parses are cached
by ``(path, mtime)`` so a burst of area requests pays for each growing hour file once.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from darkfleet.api.watch import get_watch
from darkfleet.core.tracks import Ping
from darkfleet.ingest.ais_tracks import iter_files

router = APIRouter(tags=["areas"])

ALERT_WINDOW_HOURS = 12
"""``alerts_12h`` counts findings started inside this window."""

SPARKLINE_HOURS = 12
"""The strip's sparkline: one ship count per UTC hour, oldest first."""

RECENT_MINUTES = 5.0
"""``msgs_per_min`` is measured over the recording's newest five minutes."""

HOUR_FORMAT = "%Y%m%dT%H"
"""The recorder's hour-file stem, e.g. ``20261121T09``."""

KIND_ORDER = ("dark", "sanctioned_stop", "spoof", "meeting", "jump", "radar_unmatched")
"""Alert kinds in the order the UI reasons about them; unknown kinds follow alphabetically."""


def data_dir() -> Path:
    """The recorder cache, resolved per request so tests can swap it via the environment."""
    raw = os.environ.get("DARKFLEET_AIS_DIR", "").strip()
    return Path(raw) if raw else Path("data/cache/ais")


def reset_cache() -> None:
    """Drop parsed hour statistics; tests change ``DARKFLEET_AIS_DIR`` between cases."""
    _hour_stats.cache_clear()


def _parse_iso(value: Any) -> datetime | None:
    """Parse one contract timestamp, treating a missing zone as UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    """Read ``[min_lon, min_lat, max_lon, max_lat]``, rejecting a malformed box."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    west, south, east, north = (float(item) for item in value)
    return west, south, east, north


def _inside(box: tuple[float, float, float, float], lat: float, lon: float) -> bool:
    """Edges count as inside, matching :func:`darkfleet.core.areas.area_of`."""
    west, south, east, north = box
    return west <= lon <= east and south <= lat <= north


def _ships_now(ships: list[dict[str, Any]], box: tuple[float, float, float, float]) -> int:
    """Hulls whose latest position in the snapshot falls inside the area's box."""
    count = 0
    for ship in ships:
        lat, lon = ship.get("lat"), ship.get("lon")
        if isinstance(lat, bool) or isinstance(lon, bool):
            continue
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if _inside(box, float(lat), float(lon)):
            count += 1
    return count


def _alerts_12h(alerts: list[dict[str, Any]], area_id: str, anchor: datetime) -> dict[str, int]:
    """Counts by kind for alerts attributed to this area inside the window.

    The snapshot's ``generated_at`` is the clock, not wall time: a replayed recording must
    report the window it actually describes.
    """
    cutoff = anchor - timedelta(hours=ALERT_WINDOW_HOURS)
    counts: dict[str, int] = {}
    for alert in alerts:
        if alert.get("area_id") != area_id:
            continue
        started = _parse_iso(alert.get("t_start"))
        if started is not None and started < cutoff:
            continue
        kind = alert.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: _kind_rank(item[0])))


def _kind_rank(kind: str) -> tuple[int, str]:
    """Known kinds in reading order, anything else after them by name."""
    if kind in KIND_ORDER:
        return KIND_ORDER.index(kind), kind
    return len(KIND_ORDER), kind


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Area of the intersection of two boxes; zero when they only touch."""
    west = max(a[0], b[0])
    south = max(a[1], b[1])
    east = min(a[2], b[2])
    north = min(a[3], b[3])
    if east <= west or north <= south:
        return 0.0
    return (east - west) * (north - south)


def _region_names(area: dict[str, Any], areas: list[dict[str, Any]]) -> list[str]:
    """Folder names to try for an area, most specific first.

    Listening ids match the recorder's folders (``BALTIC_WATCH`` -> ``BALTIC_WATCH``), except
    that a listening entry is suffixed when a watched box of the same name exists
    (``LACONIA_WATCH`` records into ``LACONIA``). A watched box has no folder of its own, so it
    borrows the listening box it sits in: the one with the largest bbox overlap.
    """
    raw_id = area.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        return []
    names = [raw_id]
    stripped = raw_id.removesuffix("_WATCH")
    if stripped != raw_id:
        names.append(stripped)

    if area.get("kind") == "watched":
        box = _bbox(area.get("bbox"))
        if box is not None:
            best: tuple[float, str] | None = None
            for other in areas:
                if other.get("kind") != "listening":
                    continue
                other_box = _bbox(other.get("bbox"))
                other_id = other.get("id")
                if other_box is None or not isinstance(other_id, str):
                    continue
                share = _overlap(box, other_box)
                if share > 0 and (best is None or share > best[0]):
                    best = (share, other_id)
            if best is not None:
                parent = best[1]
                for name in (parent, parent.removesuffix("_WATCH")):
                    if name not in names:
                        names.append(name)
    return names


def _region_dir(area: dict[str, Any], areas: list[dict[str, Any]], root: Path) -> Path | None:
    """The recorder folder backing an area, or None when nothing was recorded for it."""
    for name in _region_names(area, areas):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def _hour_of(path: Path) -> datetime | None:
    """The UTC hour named by a recorder file, or None for anything else."""
    try:
        return datetime.strptime(path.name[: len("20261121T09")], HOUR_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


@lru_cache(maxsize=128)
def _hour_stats(path_text: str, mtime_ns: int) -> tuple[int, float]:
    """Distinct ships and recent message rate for one hour file.

    ``mtime_ns`` is a cache key only: a file the recorder is still appending to changes it and
    is parsed again, while a closed hour is parsed once for every area that shares the region.
    """
    del mtime_ns
    pings = [obj for obj in iter_files([path_text]) if isinstance(obj, Ping)]
    if not pings:
        return 0, 0.0
    ships = len({ping.mmsi for ping in pings})
    latest = max(ping.t for ping in pings)
    cutoff = latest - timedelta(minutes=RECENT_MINUTES)
    recent = sum(1 for ping in pings if ping.t > cutoff)
    return ships, round(recent / RECENT_MINUTES, 1)


def _recording_metrics(directory: Path | None) -> tuple[float, list[int]]:
    """Recent message rate and the last twelve hourly ship counts for one region.

    Hours with no recording are reported as zero rather than skipped, so every area's
    sparkline is twelve points long and aligned to the same clock.
    """
    empty = [0] * SPARKLINE_HOURS
    if directory is None:
        return 0.0, empty

    rows: list[tuple[datetime, int, float]] = []
    try:
        paths = sorted(directory.glob("*.jsonl.gz"))
    except OSError:
        return 0.0, empty
    hours: list[tuple[datetime, Path]] = []
    for path in paths:
        hour = _hour_of(path)
        if hour is not None:
            hours.append((hour, path))
    hours.sort(key=lambda item: (item[0], item[1].name))
    for hour, path in hours[-SPARKLINE_HOURS:]:
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            continue
        ships, rate = _hour_stats(str(path), mtime_ns)
        rows.append((hour, ships, rate))
    if not rows:
        return 0.0, empty

    rows.sort(key=lambda row: row[0])
    rows = rows[-SPARKLINE_HOURS:]
    newest_hour, _, newest_rate = rows[-1]
    counts = {hour: ships for hour, ships, _ in rows}
    sparkline = [
        counts.get(newest_hour - timedelta(hours=offset), 0) for offset in range(SPARKLINE_HOURS - 1, -1, -1)
    ]
    return newest_rate, sparkline


def _area_row(
    area: dict[str, Any], all_areas: list[dict[str, Any]], snapshot: dict[str, Any]
) -> dict[str, Any]:
    """One strip tile's numbers, or zeros when the area has no usable shape."""
    area_id = area.get("id")
    name = area.get("name")
    kind = area.get("kind")
    box = _bbox(area.get("bbox"))
    anchor = _parse_iso(snapshot.get("generated_at")) or datetime.now(UTC)

    ships = snapshot.get("ships")
    alerts = snapshot.get("alerts")
    ships_list = [ship for ship in ships if isinstance(ship, dict)] if isinstance(ships, list) else []
    alerts_list = [alert for alert in alerts if isinstance(alert, dict)] if isinstance(alerts, list) else []

    ships_now = _ships_now(ships_list, box) if box is not None else 0
    by_kind = _alerts_12h(alerts_list, str(area_id), anchor) if isinstance(area_id, str) else {}
    rate, sparkline = _recording_metrics(_region_dir(area, all_areas, data_dir()))

    return {
        "id": area_id,
        "name": name,
        "kind": kind,
        "bbox": list(box) if box is not None else None,
        "ships_now": ships_now,
        "alerts_12h": by_kind,
        "msgs_per_min": rate,
        "sparkline": sparkline,
    }


@router.get("/api/areas/stats")
def get_area_stats() -> dict[str, Any]:
    """One stats row per ``watch.json`` area, in the snapshot's own order.

    A missing snapshot is the watch router's 503: the strip and the map should fail together
    rather than one claiming there are no areas.
    """
    snapshot = get_watch()
    areas = snapshot.get("areas")
    area_list = [area for area in areas if isinstance(area, dict)] if isinstance(areas, list) else []
    return {
        "generated_at": snapshot.get("generated_at"),
        "areas": [_area_row(area, area_list, snapshot) for area in area_list],
    }
