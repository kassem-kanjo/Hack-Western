"""Ship search API (issue #183): one query, at most twenty hulls from the snapshot.

The watch screen already serves the whole fleet; this router is the keyboard-first way into
it. It reads the same frozen snapshot ``api/watch.py`` serves (``DARKFLEET_DATA`` / watch.json)
and keeps no state beyond the per-mtime parse, so a rebuild is visible on the next request.

The Hormuz story ships (``hormuz_stories.json``, issue #219) are indexed beside the watch
fleet: they are hulls the Watch map never shows, but the analyst still needs to find them.
A story hit carries ``source: "hormuz_story"`` and its last known position, so the UI can send
it to the Hormuz screen instead of the map.

Matching is deliberately narrow: names match case-insensitively on an exact value, a prefix or
a substring - an exact whole name sorts first. MMSI and IMO only match on an exact value or a
prefix. A one-character query is noise, so it returns nothing. Sanctioned hulls sort first
within each exact-name class; the rest follow match quality and name. A missing snapshot is an
empty list rather than an error: search is an entry point, not the screen's only source of
truth.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from darkfleet.api.watch import data_dir, watch_path

router = APIRouter(tags=["search"])

# The result list has to stay scannable at a glance; the UI never pages it.
MAX_RESULTS = 20

# One character matches most of the fleet and tells the analyst nothing.
MIN_QUERY = 2

# Watchlist hulls first: sanctions are the one label that changes the reading of a row.
WATCH_RANK = {"sanctioned": 0, "shadow_fleet": 1}
OTHER_WATCH_RANK = 2

# Hormuz story ships are listed by definition; their OpenSanctions risk tags map onto the
# same two labels the watch fleet already uses.
STORIES_FILE = "hormuz_stories.json"
STORY_WATCHLIST = {"sanction": "sanctioned", "mare.shadow": "shadow_fleet"}


@lru_cache(maxsize=8)
def _load_watch(path: str, mtime_ns: int) -> dict[str, Any] | None:
    """Parse watch.json once per modification; a rewrite changes the cache key."""
    del mtime_ns  # cache key only
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@lru_cache(maxsize=8)
def _load_stories(path: str, mtime_ns: int) -> dict[str, Any] | None:
    """Parse hormuz_stories.json once per modification; a missing file is just None."""
    del mtime_ns  # cache key only
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def reset_cache() -> None:
    """Drop the parsed snapshots; tests swap DARKFLEET_DATA between cases."""
    _load_watch.cache_clear()
    _load_stories.cache_clear()


def _snapshot() -> dict[str, Any] | None:
    """The current watch snapshot, or None before the first build."""
    path = watch_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _load_watch(str(path), mtime_ns)


def _stories_snapshot() -> dict[str, Any] | None:
    """The current Hormuz story snapshot, or None when it was never built."""
    path = data_dir() / STORIES_FILE
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _load_stories(str(path), mtime_ns)


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
    """0 for an exact whole name, 2 for a prefix, 3 for a substring, None otherwise."""
    if name is None:
        return None
    folded = name.casefold()
    if folded == query:
        return 0
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


def _story_watchlist(ship: dict[str, Any]) -> str | None:
    """Map a listed ship's OpenSanctions risk tags onto the watch fleet's two labels."""
    tags = ship.get("lists")
    if not isinstance(tags, list):
        return None
    for tag in tags:
        if isinstance(tag, str) and tag in STORY_WATCHLIST:
            return STORY_WATCHLIST[tag]
    return None


def _candidates(
    payload: dict[str, Any] | None, stories: dict[str, Any] | None
) -> list[tuple[dict[str, Any], float, float, str, str | None]]:
    """Every hull search may consider, with its position and where it came from."""
    candidates: list[tuple[dict[str, Any], float, float, str, str | None]] = []

    for ship in payload.get("ships", []) if payload is not None else []:
        if not isinstance(ship, dict):
            continue
        lat = _coordinate(ship.get("lat"))
        lon = _coordinate(ship.get("lon"))
        mmsi_text = _digits(ship.get("mmsi"))
        if lat is None or lon is None or mmsi_text is None:
            continue
        candidates.append((ship, lat, lon, mmsi_text, None))

    # Story hulls are only known where they came back on the transponder.
    for ship in stories.get("listed", []) if stories is not None else []:
        if not isinstance(ship, dict):
            continue
        position = ship.get("on")
        if not isinstance(position, dict):
            continue
        lat = _coordinate(position.get("lat"))
        lon = _coordinate(position.get("lon"))
        mmsi_text = _digits(ship.get("mmsi"))
        if lat is None or lon is None or mmsi_text is None:
            continue
        candidates.append((ship, lat, lon, mmsi_text, "hormuz_story"))

    return candidates


def search_ships(
    payload: dict[str, Any] | None,
    query: str,
    stories: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Match one query against the snapshot's ships, exact names first, at most 20 rows."""
    folded = query.strip().casefold()
    if len(folded) < MIN_QUERY:
        return []

    digits = folded if folded.isdigit() else None
    areas = _areas(payload) if payload is not None else []
    ranked: list[tuple[int, int, int, str, int, dict[str, Any]]] = []

    for ship, lat, lon, mmsi_text, source in _candidates(payload, stories):
        rank = _match_rank(ship, folded, digits)
        if rank is None:
            continue

        name = _text(ship.get("name"))
        exact = 0 if name is not None and name.casefold() == folded else 1
        watchlist = _story_watchlist(ship) if source is not None else _text(ship.get("watchlist"))
        row: dict[str, Any] = {
            "mmsi": int(mmsi_text),
            "name": name,
            "imo": _text(ship.get("imo")),
            "type": _text(ship.get("type")) or _text(ship.get("class")),
            "watchlist": watchlist,
            "lat": lat,
            "lon": lon,
            "area": _area_for(areas, lat, lon),
        }
        if source is not None:
            row["source"] = source

        ranked.append(
            (
                exact,
                WATCH_RANK.get(watchlist or "", OTHER_WATCH_RANK),
                rank,
                name.casefold() if name is not None else "\uffff",
                int(mmsi_text),
                row,
            )
        )

    ranked.sort(key=lambda row: row[:5])
    return [row[5] for row in ranked[:MAX_RESULTS]]


@router.get("/api/search")
def search(q: str = "") -> list[dict[str, Any]]:
    """Ship search for the top-bar field; an empty query is an empty list, not an error."""
    return search_ships(_snapshot(), q, _stories_snapshot())
