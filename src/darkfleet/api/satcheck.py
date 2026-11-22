"""Per-alert satellite check API (issue #268): one JSON file plus its chip images.

A script writes ``<DATA>/satcheck/<alert_id>.json`` and ``<DATA>/satcheck/<alert_id>/NNN.png``
after a dark event; these read-only routes serve them back unchanged so the console can show the
SAR verdict next to the alert. ``DARKFLEET_DATA`` is re-read per request, so a fresh run appears
without a restart, and the id patterns are checked before any path is built from a URL segment.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/satcheck", tags=["satcheck"])

ALERT_ID = re.compile(r"^[a-z_]{3,20}-[0-9]{1,12}-[0-9]{1,12}$")  # no dots or slashes: no traversal
CHIP_NAME = re.compile(r"^[0-9]{3}\.png$")
NOT_READY = "no satellite check for this event yet"
MALFORMED = "satellite check file is malformed"


def data_dir() -> Path:
    """Resolve the snapshot directory per request, matching the other api routers."""
    return Path(os.environ.get("DARKFLEET_DATA", "data/snapshot"))


def satcheck_dir() -> Path:
    """The one subdirectory this router serves."""
    return data_dir() / "satcheck"


@router.get("/{alert_id}")
def get_satcheck(alert_id: str, response: Response) -> dict[str, Any]:
    """Serve the check untouched; an unknown or malformed id is a 404 before any file is read."""
    if ALERT_ID.fullmatch(alert_id) is None:
        raise HTTPException(status_code=404, detail=NOT_READY, headers={"Cache-Control": "no-cache"})
    path = satcheck_dir() / f"{alert_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=NOT_READY, headers={"Cache-Control": "no-cache"})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=MALFORMED) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=MALFORMED)
    response.headers["Cache-Control"] = "no-cache"
    return payload


@router.get("/{alert_id}/chip/{name:path}")
def get_chip(alert_id: str, name: str) -> FileResponse:
    """Serve one chip PNG; strict patterns plus a containment check keep the path local."""
    if ALERT_ID.fullmatch(alert_id) is None or CHIP_NAME.fullmatch(name) is None:
        raise HTTPException(status_code=404, detail="chip not found")
    base = satcheck_dir().resolve()
    path = (base / alert_id / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(status_code=404, detail="chip not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )
