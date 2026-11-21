"""Our own ship detections from fresh Sentinel-1 radar (issue #90).

``GET /api/sar/scan`` serves ``sar_scan.json`` (``scripts/sar_scan.py``); ``GET /api/sar/chip/{name}.png``
serves the small radar / optical crops next to it. Both are read from ``DARKFLEET_DATA``; a missing
file is a 404 JSON body so the console can hide the block.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/sar", tags=["sar"])

SCAN_FILE = "sar_scan.json"
CHIP_NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")  # no dots or slashes: no path traversal


def data_dir() -> Path:
    return Path(os.environ.get("DARKFLEET_DATA", "data/snapshot"))


@router.get("/scan")
def scan() -> dict[str, Any]:
    path = data_dir() / SCAN_FILE
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no radar scan yet")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="sar_scan.json must be an object")
    return payload


@router.get("/chip/{name}.png")
def chip(name: str) -> FileResponse:
    if not CHIP_NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail="chip not found")
    path = data_dir() / "chips" / f"{name}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="chip not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})
