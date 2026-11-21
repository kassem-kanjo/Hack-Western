"""Hormuz snapshot API (#88/#89): the undeclared fleet and its history.

Two frozen files back the Global screen's Hormuz block:

``hormuz_unseen.json``
    Daily radar counts versus AIS broadcasts and the density grid of radar
    detections that matched no broadcast (the "Radar, not broadcasting" layer).
``hormuz_history.json``
    GFW encounter and loitering events plus the ranked loitering hotspots.

Both are read straight from ``DARKFLEET_DATA``; a missing file is a 404 with a
JSON body, never a stack trace, so the console can hide the block while the rest
of the snapshot still works.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/hormuz", tags=["hormuz"])

UNSEEN_FILE = "hormuz_unseen.json"
HISTORY_FILE = "hormuz_history.json"


def data_dir() -> Path:
    """Resolve the snapshot directory per request so tests can point at a temp dir."""
    return Path(os.environ.get("DARKFLEET_DATA", "data/snapshot"))


@lru_cache(maxsize=16)
def _load_snapshot(path: str, mtime_ns: int) -> dict[str, Any]:
    """Parse one snapshot file once per modification; the mtime key makes rebuilds visible."""
    del mtime_ns  # cache key only: a rewrite changes the key and therefore reparses
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be an object, got {type(payload).__name__}")
    return payload


def reset_cache() -> None:
    """Drop parsed snapshots; tests change DARKFLEET_DATA between cases."""
    _load_snapshot.cache_clear()


def _snapshot(name: str) -> dict[str, Any]:
    """Serve one snapshot file, or answer 404 JSON when it was never built."""
    path = data_dir() / name
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"snapshot missing: {name}") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"snapshot missing: {name}")
    return _load_snapshot(str(path), mtime_ns)


@router.get("/unseen")
def get_unseen() -> dict[str, Any]:
    """Daily dark share, the density grid and the headline summary for the strait."""
    return _snapshot(UNSEEN_FILE)


@router.get("/history")
def get_history() -> dict[str, Any]:
    """GFW encounters, loitering events and the hotspot ranking for the strait."""
    return _snapshot(HISTORY_FILE)
