"""Edge-unit status for the console: local file today, the Pi's HTTP view when configured.

The browser never calls the Pi. When ``EDGE_HOST`` is set this router fetches
``http://<EDGE_HOST>:7071/edge_status.json`` with a one-second timeout and falls
back to the local supervisor status file. ``source`` says which one answered.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/edge", tags=["edge"])

STATUS_FILE = "edge_status.json"
EDGE_STATUS_PORT = 7071
EDGE_TIMEOUT_S = 1.0
DEFAULT_EDGE_DIR = "data/edge"


def status_path() -> Path:
    """Local status file; tests point EDGE_STATUS_PATH at a temp file."""
    raw = os.environ.get("EDGE_STATUS_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(os.environ.get("DARKFLEET_EDGE_DIR", DEFAULT_EDGE_DIR)) / STATUS_FILE


def edge_host() -> str:
    """The Pi host, or an empty string when the console should stay local."""
    return os.environ.get("EDGE_HOST", "").strip()


def _read_local() -> dict[str, Any] | None:
    try:
        payload = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _fetch_remote(host: str) -> dict[str, Any] | None:
    """One bounded request to the Pi's status_http server; None on any failure."""
    url = f"http://{host}:{EDGE_STATUS_PORT}/edge_status.json"
    try:
        response = httpx.get(url, timeout=EDGE_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Return the newest status we can get, labelled with where it came from."""
    host = edge_host()
    if host:
        remote = _fetch_remote(host)
        if remote is not None:
            return {**remote, "source": "pi"}

    local = _read_local()
    if local is not None:
        return {**local, "source": "local"}
    return {"source": "local", "state": "UNKNOWN", "error": "edge status unavailable"}
