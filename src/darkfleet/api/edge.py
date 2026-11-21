"""Edge-unit status, frame and operator controls for the console.

The browser never calls the Pi. When ``EDGE_HOST`` is set this router fetches
``http://<EDGE_HOST>:7071/edge_status.json`` (and ``/latest.jpg``) with a one-second
timeout and falls back to the local supervisor files. ``source`` says which one answered.

Kill and stress are demo-only write paths: enabled solely by ``EDGE_DEMO_CONTROLS=1``.
Locally they drop a control file the supervisor watches; with ``EDGE_HOST`` set they are
forwarded to the Pi's ``status_http`` as ``POST /control/kill`` / ``POST /control/stress``,
carrying ``EDGE_STATUS_TOKEN`` in the query when one is configured.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/api/edge", tags=["edge"])

STATUS_FILE = "edge_status.json"
LATEST_FRAME = "latest.jpg"
DECISION_FILE = "latest_decision.json"
AB_FILE = "ab_result.json"
CONTROL_DIR = "control"
KILL_FILE = "kill_detector"
STRESS_FILE = "stress_ai"
STRESS_SECONDS = 10.0
EDGE_STATUS_PORT = 7071
EDGE_TIMEOUT_S = 1.0
CONTROL_TIMEOUT_S = 2.0
# A hostile or broken peer must not be able to fill the API's memory or hold the request open:
# status is tiny JSON, a frame is one JPEG, and everything must arrive within two seconds.
STATUS_MAX_BYTES = 64 << 10
FRAME_MAX_BYTES = 2 << 20
DECISION_MAX_BYTES = 64 << 10
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


def decision_path() -> Path:
    """The per-frame decision sits next to the status file, like the frame."""
    return status_path().parent / DECISION_FILE


def kill_path() -> Path:
    return status_path().parent / CONTROL_DIR / KILL_FILE


def stress_path() -> Path:
    return status_path().parent / CONTROL_DIR / STRESS_FILE


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


def _fetch_remote_decision(host: str) -> dict[str, Any] | None:
    """The Pi's latest decision object, or None within the same bounded budget as status."""
    url = _edge_url(host, DECISION_FILE)
    try:
        body = _relay(url, DECISION_MAX_BYTES)
        payload = json.loads(body)
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


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


@router.get("/decision")
def get_decision() -> dict[str, Any]:
    """The per-frame verdict: local file, or relayed from the Pi exactly like the frame."""
    host = edge_host()
    payload: dict[str, Any] | None = None
    if host:
        try:
            payload = _fetch_remote_decision(host)
        except _RelayTooLarge:
            raise HTTPException(status_code=502, detail="edge decision too large") from None
    else:
        try:
            local = json.loads(decision_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            local = None
        payload = local if isinstance(local, dict) else None
    if payload is None:
        raise HTTPException(status_code=404, detail="no decision")
    return payload


def _sighting_feed_item(rec: dict[str, Any]) -> dict[str, Any]:
    """One log record as a Watch-feed item (contract: docs/watch-contract.md, kind patrol_sighting)."""
    patrol = rec.get("patrol") or {}
    return {
        "kind": "patrol_sighting",
        "id": str(rec.get("hash", ""))[:16],
        "t": rec.get("t_utc"),
        "lat": patrol.get("lat"),
        "lon": patrol.get("lon"),
        "title": "Vessel not on AIS",
        "detail": f"Seen by the patrol unit at {patrol.get('name', 'patrol')}; no AIS ship within range.",
        "trend": rec.get("trend"),
        "frame_sha256": rec.get("frame_sha256"),
        "seq": rec.get("seq"),
    }


@router.get("/sightings")
def get_sightings() -> dict[str, Any]:
    """The Pi's hash-chained sightings log: chain status, last records, and Watch feed items."""
    host = edge_host()
    payload: dict[str, Any] | None = None
    if host:
        try:
            payload = json.loads(_relay(_edge_url(host, "sightings.json"), DECISION_MAX_BYTES * 4))
        except (httpx.HTTPError, ValueError, _RelayTooLarge):
            payload = None
    else:
        from darkfleet.edge import sightings

        path = str(status_path().parent / sightings.FILE_NAME)
        records, _torn = sightings.read_records(path)
        if records:
            payload = {"chain": sightings.verify(path), "sightings": list(reversed(records[-20:]))}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail="no sightings log")
    items = [_sighting_feed_item(r) for r in payload.get("sightings", []) if "hash" in r]
    return {"chain": payload.get("chain"), "sightings": payload.get("sightings", []), "feed": items}


@router.get("/ab")
def get_ab() -> dict[str, Any]:
    """The last priority-guard A/B result (written by the Pi's ab_test), or 404 if none yet."""
    host = edge_host()
    payload: dict[str, Any] | None = None
    if host:
        try:
            payload = json.loads(_relay(_edge_url(host, AB_FILE), DECISION_MAX_BYTES))
        except (httpx.HTTPError, ValueError, _RelayTooLarge):
            payload = None
    else:
        try:
            payload = json.loads((status_path().parent / AB_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail="no A/B result yet")
    return payload


CONTROL_HEADER = "x-darkfleet-control"


def _require_control_allowed(request: Request) -> str | None:
    """Gate the two write paths; returns the Pi host (or None for a local unit).

    1. ``EDGE_DEMO_CONTROLS=1`` must be set.
    2. A remote unit (``EDGE_HOST``) additionally needs ``EDGE_STATUS_TOKEN``: the Pi's status
       server then refuses control calls that do not carry it.
    3. The request must come from our own page: a custom header (a cross-site form cannot set
       one) and, when the browser sent an Origin, the same host as this request.
    """
    if not controls_enabled():
        raise HTTPException(status_code=403, detail="controls disabled")
    host = edge_host()
    if host and not status_token():
        raise HTTPException(status_code=403, detail="remote controls need EDGE_STATUS_TOKEN")
    if request.headers.get(CONTROL_HEADER) != "1":
        raise HTTPException(status_code=403, detail="control header missing")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="cross-origin control refused")
    return host


@router.post("/kill", status_code=202)
def kill_detector(request: Request) -> dict[str, Any]:
    """Ask the supervisor to SIGKILL the detector; recovery then follows on its own."""
    host = _require_control_allowed(request)
    if host:
        if not _forward_control(host, "control/kill"):
            raise HTTPException(status_code=502, detail="edge did not accept the control")
        return {"ok": True, "target": "pi", "requested_at": time.time()}

    try:
        _write_control(kill_path(), b"1")
    except OSError:
        raise HTTPException(status_code=503, detail="cannot write control file") from None
    return {"ok": True, "target": "local", "requested_at": time.time()}


@router.post("/stress", status_code=202)
def stress_detector(request: Request) -> dict[str, Any]:
    """Simulate the AI hogging every core for ten seconds (demo only)."""
    host = _require_control_allowed(request)
    if host:
        if not _forward_control(host, "control/stress", {"seconds": STRESS_SECONDS}):
            raise HTTPException(status_code=502, detail="edge did not accept the control")
        return {
            "ok": True,
            "target": "pi",
            "seconds": STRESS_SECONDS,
            "requested_at": time.time(),
        }

    try:
        _write_control(stress_path(), f"{STRESS_SECONDS:g}".encode())
    except OSError:
        raise HTTPException(status_code=503, detail="cannot write control file") from None
    return {
        "ok": True,
        "target": "local",
        "seconds": STRESS_SECONDS,
        "requested_at": time.time(),
    }


def _write_control(path: Path, payload: bytes) -> None:
    """Replace a control file atomically; the supervisor consumes it exactly once."""
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _forward_control(host: str, route: str, payload: dict[str, Any] | None = None) -> bool:
    """POST one demo control to the Pi's status_http; False on any failure.

    The response body is not relayed: the console only needs to know it was accepted.
    """
    try:
        response = httpx.post(
            _edge_url(host, route),
            json=payload or {},
            timeout=CONTROL_TIMEOUT_S,
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True
