"""Edge-unit status, frame and operator kill for the console.

The browser never calls the Pi. When ``EDGE_HOST`` is set this router fetches
``http://<EDGE_HOST>:7071/edge_status.json`` (and ``/latest.jpg``) with a one-second
timeout and falls back to the local supervisor files. ``source`` says which one answered.

Kill is a demo-only write path: it is enabled solely by ``EDGE_DEMO_CONTROLS=1`` and only
when the edge is local. The supervisor watches the control file and kills the detector.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/api/edge", tags=["edge"])

STATUS_FILE = "edge_status.json"
LATEST_FRAME = "latest.jpg"
CONTROL_DIR = "control"
KILL_FILE = "kill_detector"
EDGE_STATUS_PORT = 7071
EDGE_TIMEOUT_S = 1.0
# A hostile or broken peer must not be able to fill the API's memory or hold the request open:
# status is tiny JSON, a frame is one JPEG, and everything must arrive within two seconds.
STATUS_MAX_BYTES = 64 << 10
FRAME_MAX_BYTES = 2 << 20
RELAY_DEADLINE_S = 2.0
DEFAULT_EDGE_DIR = "data/edge"


class _RelayTooLarge(Exception):
    """The peer sent more bytes than the endpoint may return."""


def status_token() -> str | None:
    """Shared secret for the Pi's status server, if the deployment set one."""
    raw = os.environ.get("EDGE_STATUS_TOKEN", "").strip()
    return raw or None


def status_path() -> Path:
    """Local status file; tests point EDGE_STATUS_PATH at a temp file."""
    raw = os.environ.get("EDGE_STATUS_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(os.environ.get("DARKFLEET_EDGE_DIR", DEFAULT_EDGE_DIR)) / STATUS_FILE


def latest_path() -> Path:
    """The annotated frame sits next to the status file the supervisor writes."""
    return status_path().parent / LATEST_FRAME


def kill_path() -> Path:
    return status_path().parent / CONTROL_DIR / KILL_FILE


def edge_host() -> str:
    """The Pi host, or an empty string when the console should stay local."""
    return os.environ.get("EDGE_HOST", "").strip()


def controls_enabled() -> bool:
    """Operator writes are off unless the demo explicitly turns them on."""
    return os.environ.get("EDGE_DEMO_CONTROLS", "").strip() == "1"


def _edge_url(host: str, name: str) -> str:
    """Build the Pi's URL, carrying the shared token in the query when one is configured."""
    url = f"http://{host}:{EDGE_STATUS_PORT}/{name}"
    token = status_token()
    if token:
        url = f"{url}?{urlencode({'token': token})}"
    return url


def _relay(url: str, max_bytes: int, deadline_s: float | None = None) -> bytes:
    """Stream one Pi response under a hard byte cap and a total (not per-read) deadline.

    ``httpx``'s timeout only bounds each individual network operation, so a peer dripping one
    byte at a time would otherwise stream forever. Raises :class:`_RelayTooLarge` past the cap.
    """
    if deadline_s is None:
        deadline_s = RELAY_DEADLINE_S
    started = time.monotonic()
    body = bytearray()
    with httpx.stream("GET", url, timeout=EDGE_TIMEOUT_S, follow_redirects=False) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if time.monotonic() - started > deadline_s:
                raise httpx.TimeoutException(f"edge relay exceeded {deadline_s}s")
            if len(body) + len(chunk) > max_bytes:
                raise _RelayTooLarge(url)
            body.extend(chunk)
    return bytes(body)


def _read_local() -> dict[str, Any] | None:
    try:
        payload = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _fetch_remote(host: str) -> dict[str, Any] | None:
    """One bounded request to the Pi's status_http server; None on any failure."""
    url = _edge_url(host, "edge_status.json")
    try:
        body = _relay(url, STATUS_MAX_BYTES)
        payload = json.loads(body)
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _fetch_remote_frame(host: str) -> bytes | None:
    """The Pi's latest annotated frame, or None within the same bounded budget."""
    url = _edge_url(host, "latest.jpg")
    try:
        content = _relay(url, FRAME_MAX_BYTES)
    except httpx.HTTPError:
        return None
    return content or None


def _frame_available(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Return the newest status we can get, labelled with where it came from."""
    host = edge_host()
    controls = controls_enabled()
    if host:
        try:
            remote = _fetch_remote(host)
        except _RelayTooLarge:
            raise HTTPException(status_code=502, detail="edge status too large") from None
        if remote is not None:
            return {
                **remote,
                "source": "pi",
                "controls": controls,
                "frame_available": bool(remote.get("frame_available", True)),
            }

    local = _read_local()
    available = _frame_available(latest_path())
    if local is not None:
        return {**local, "source": "local", "controls": controls, "frame_available": available}
    return {
        "source": "local",
        "state": "UNKNOWN",
        "error": "edge status unavailable",
        "controls": controls,
        "frame_available": available,
    }


@router.get("/frame.jpg")
def get_frame() -> Response:
    """The latest annotated frame, straight from the Pi or the local state directory."""
    host = edge_host()
    if host:
        try:
            body = _fetch_remote_frame(host)
        except _RelayTooLarge:
            raise HTTPException(status_code=502, detail="edge frame too large") from None
    else:
        try:
            body = latest_path().read_bytes()
        except OSError:
            body = None
    if not body:
        raise HTTPException(status_code=404, detail="no frame")
    return Response(
        content=body,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/kill", status_code=202)
def kill_detector() -> dict[str, Any]:
    """Ask the supervisor to SIGKILL the detector; recovery then follows on its own."""
    if not controls_enabled():
        raise HTTPException(status_code=403, detail="controls disabled")
    if edge_host():
        raise HTTPException(status_code=409, detail="edge is remote")

    path = kill_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(b"1")
        os.replace(tmp, path)
    except OSError:
        raise HTTPException(status_code=503, detail="cannot write control file") from None
    return {"ok": True, "requested_at": time.time()}
