"""Ship search API (issue #183): one query, at most twenty hulls from ``watch.json``.

The watch screen already serves the whole fleet; this router is the keyboard-first way into
it. It reads the same frozen snapshot ``api/watch.py`` serves (``DARKFLEET_DATA`` / watch.json)
and keeps no state beyond the per-mtime parse, so a rebuild is visible on the next request.

Matching is deliberately narrow: names match case-insensitively on a prefix or a substring,
MMSI and IMO only on an exact value or a prefix. A one-character query is noise, so it
returns nothing. Sanctioned hulls sort first; the rest follow match quality and name. A
missing snapshot is an empty list rather than an error: search is an entry point, not the
screen's only source of truth.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from darkfleet.api.watch import watch_path

router = APIRouter(tags=["search"])

# The result list has to stay scannable at a glance; the UI never pages it.
MAX_RESULTS = 20

# One character matches most of the fleet and tells the analyst nothing.
MIN_QUERY = 2

# Watchlist hulls first: sanctions are the one label that changes the reading of a row.
WATCH_RANK = {"sanctioned": 0, "shadow_fleet": 1}
OTHER_WATCH_RANK = 2


@lru_cache(maxsize=8)
def _load_watch(path: str, mtime_ns: int) -> dict[str, Any] | None:
    """Parse watch.json once per modification; a rewrite changes the cache key."""
    del mtime_ns  # cache key only
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def reset_cache() -> None:
    """Drop the parsed snapshot; tests swap DARKFLEET_DATA between cases."""
    _load_watch.cache_clear()


def _snapshot() -> dict[str, Any] | None:
    """The current watch snapshot, or None before the first build."""
    path = watch_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _load_watch(str(path), mtime_ns)


def _text(value: object) -> str | None:
    """A trimmed non-empty string, or None for anything else."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _digits(value: object) -> str | None:
    """An identity as digits: MMSI as an integer, IMO as a string or an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    text = _text(value)
    if text is None:
        return None
    return text if text.isdigit() else None


def _coordinate(value: object) -> float | None:
    """A finite number, or None when the snapshot field cannot be a position."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _name_rank(name: str | None, query: str) -> int | None:
    """2 for a name prefix, 3 for a name substring, None when the name does not match."""
    if name is None:
        return None
    folded = name.casefold()
    if folded.startswith(query):
        return 2
    if query in folded:
        return 3
    return None


def _identity_rank(ship: dict[str, Any], query: str) -> int | None:
    """MMSI/IMO rank for a digits-only query; exact beats prefix."""
    best: int | None = None
    for key in ("mmsi", "imo"):
        identity = _digits(ship.get(key))
        if identity is None:
            continue
        if identity == query:
            return 0
        if identity.startswith(query):
            best = 1
    return best


def _match_rank(ship: dict[str, Any], query: str, digits: str | None) -> int | None:
    """The best match class for one hull, or None when it does not match at all."""
    ranks: list[int] = []
    name_rank = _name_rank(_text(ship.get("name")), query)
    if name_rank is not None:
        ranks.append(name_rank)
    if digits is not None:
        identity_rank = _identity_rank(ship, digits)
        if identity_rank is not None:
            ranks.append(identity_rank)
    return min(ranks) if ranks else None


def _areas(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Watched areas first, so a hull on a watched/listening border names the watched one."""
    areas = [area for area in payload.get("areas", []) if isinstance(area, dict)]
    return sorted(areas, key=lambda area: 0 if area.get("kind") == "watched" else 1)


def _area_for(areas: list[dict[str, Any]], lat: float, lon: float) -> str | None:
    """The id of the most specific area containing the fix: watched wins over listening."""
    best: tuple[int, float, str] | None = None
    for area in areas:
        bbox = area.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox):
            continue
        west, south, east, north = (float(value) for value in bbox)
        if not (west <= lon <= east and south <= lat <= north):
            continue
        area_id = _text(area.get("id"))
        if area_id is None:
            continue
        kind_rank = 0 if area.get("kind") == "watched" else 1
        key = (kind_rank, abs(east - west) * abs(north - south), area_id)
        if best is None or key < best:
            best = key
    return None if best is None else best[2]


def search_ships(payload: dict[str, Any] | None, query: str) -> list[dict[str, Any]]:
    """Match one query against the snapshot's ships, sanctioned first, at most 20 rows."""
    folded = query.strip().casefold()
    if payload is None or len(folded) < MIN_QUERY:
        return []

    digits = folded if folded.isdigit() else None
    areas = _areas(payload)
    ranked: list[tuple[int, int, str, int, dict[str, Any]]] = []

    for ship in payload.get("ships", []):
        if not isinstance(ship, dict):
            continue
        lat = _coordinate(ship.get("lat"))
        lon = _coordinate(ship.get("lon"))
        mmsi_text = _digits(ship.get("mmsi"))
        if lat is None or lon is None or mmsi_text is None:
            continue

        rank = _match_rank(ship, folded, digits)
        if rank is None:
            continue

        name = _text(ship.get("name"))
        watchlist = _text(ship.get("watchlist"))
        ranked.append(
            (
                WATCH_RANK.get(watchlist or "", OTHER_WATCH_RANK),
                rank,
                name.casefold() if name is not None else "\uffff",
                int(mmsi_text),
                {
                    "mmsi": int(mmsi_text),
                    "name": name,
                    "imo": _text(ship.get("imo")),
                    "type": _text(ship.get("type")) or _text(ship.get("class")),
                    "watchlist": watchlist,
                    "lat": lat,
                    "lon": lon,
                    "area": _area_for(areas, lat, lon),
                },
            )
        )

    ranked.sort(key=lambda row: row[:4])
    return [row[4] for row in ranked[:MAX_RESULTS]]


@router.get("/api/search")
def search(q: str = "") -> list[dict[str, Any]]:
    """Ship search for the top-bar field; an empty query is an empty list, not an error."""
    return search_ships(_snapshot(), q)
