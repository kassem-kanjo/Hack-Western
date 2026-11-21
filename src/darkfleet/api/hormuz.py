"""Hormuz snapshot API (#88/#89/#197): the undeclared fleet and its history.

Frozen files back the Global screen's Hormuz block:

``hormuz_unseen.json``
    Daily radar counts versus AIS broadcasts and the density grid of radar
    detections that matched no broadcast (the "Radar, not broadcasting" layer).
``hormuz_history.json``
    GFW encounter and loitering events plus the ranked loitering hotspots.
``hormuz_unseen_long.json`` / ``oman_control_long.json``
    Two years of daily radar counts for the strait and for an open Gulf of Oman
    control box, pooled to weeks by ``/api/hormuz/trend``.
``hormuz_validation.json``
    The event study and the overall regime comparison against that control.

All are read straight from ``DARKFLEET_DATA``; a missing file is a 404 with a
JSON body, never a stack trace, so the console can hide the block while the rest
of the snapshot still works.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/hormuz", tags=["hormuz"])

UNSEEN_FILE = "hormuz_unseen.json"
HISTORY_FILE = "hormuz_history.json"
TREND_HORMUZ_FILE = "hormuz_unseen_long.json"
TREND_CONTROL_FILE = "oman_control_long.json"
TREND_VALIDATION_FILE = "hormuz_validation.json"

# The validation's coverage rule: a day with fewer radar detections than this is
# a partial satellite pass, not a measurement, and stays out of every pool.
MIN_SEEN = 20
# Event fields the trend panel draws; anything else in the validation file stays
# on the file, so the API contract cannot drift with an edit to the report.
EVENT_FIELDS = ("date_start", "date_end", "title", "type", "source_url", "verdict")


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


@router.get("/stories")
def get_stories() -> dict[str, Any]:
    """Listed ships that went dark in the Gulf box, and the outage clusters (#219)."""
    return _snapshot("hormuz_stories.json")


def _number(value: Any) -> float | None:
    """A finite JSON number, or None for anything else (bools are not counts)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _week_start(day: str) -> str | None:
    """Monday of the ISO week containing ``day``; None when the date is unreadable."""
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return None
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def _weekly(daily: Any) -> list[dict[str, Any]]:
    """Pool daily counts into weeks: sum dark / sum seen over days with seen >= 20.

    Pooling counts rather than averaging daily shares keeps a quiet day from
    carrying the same weight as a full satellite pass.
    """
    pools: dict[str, dict[str, int]] = {}
    if not isinstance(daily, list):
        return []

    for row in daily:
        if not isinstance(row, dict):
            continue
        day = row.get("day")
        seen = _number(row.get("seen"))
        dark = _number(row.get("dark"))
        if not isinstance(day, str) or seen is None or dark is None:
            continue
        if seen < MIN_SEEN or dark < 0:
            continue
        week = _week_start(day)
        if week is None:
            continue
        pool = pools.setdefault(week, {"seen": 0, "dark": 0})
        pool["seen"] += int(seen)
        pool["dark"] += int(dark)

    return [
        {
            "week": week,
            "seen": pool["seen"],
            "dark": pool["dark"],
            "dark_share": pool["dark"] / pool["seen"] if pool["seen"] else 0.0,
        }
        for week, pool in sorted(pools.items())
    ]


def _share(node: Any) -> float | None:
    """The dark share out of one pre/post block, or None when it was not measured."""
    if not isinstance(node, dict):
        return None
    return _number(node.get("dark_share"))


def _prepost(node: Any) -> dict[str, float | None]:
    """The two shares the panel prints for one area around an event."""
    block = node if isinstance(node, dict) else {}
    return {"pre": _share(block.get("pre")), "post": _share(block.get("post"))}


def _event(row: Any) -> dict[str, Any] | None:
    """Project one validation event onto the fields the trend panel draws.

    The pre/post shares travel with the event so the ceasefire row can print
    measured numbers without the UI reading the validation report itself.
    """
    if not isinstance(row, dict):
        return None
    projected = {field: row.get(field) for field in EVENT_FIELDS}
    projected["treated"] = _prepost(row.get("treated"))
    projected["control"] = _prepost(row.get("control"))
    return projected


@router.get("/trend")
def get_trend() -> dict[str, Any]:
    """Two years of weekly dark share: Hormuz against its Gulf of Oman control.

    The treated and control series pool their own daily counts at ``MIN_SEEN``;
    events and the overall regime come from the validation file unchanged, so
    the chart and the headline cannot disagree about what was measured.
    """
    treated = _snapshot(TREND_HORMUZ_FILE)
    control = _snapshot(TREND_CONTROL_FILE)
    validation = _snapshot(TREND_VALIDATION_FILE)

    rows = validation.get("events")
    events = [_event(row) for row in rows] if isinstance(rows, list) else []
    regime = validation.get("regime")
    return {
        "treated": _weekly(treated.get("daily")),
        "control": _weekly(control.get("daily")),
        "events": [event for event in events if event is not None],
        "regime": regime if isinstance(regime, dict) else None,
    }
