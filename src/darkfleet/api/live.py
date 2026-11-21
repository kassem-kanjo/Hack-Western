"""Live-region API (#80): snapshot reads plus an SSE tail of the AIS recorder.

The demo has two data sources with different lifetimes: ``live_<REGION>.json`` is rebuilt
periodically by the snapshot job, while raw aisstream lines keep landing in the recorder's
gzip files. Serving both from one router lets the UI draw a consistent snapshot and then
advance it live, without a database or a second service.

Everything here is read-only and file-backed, following ``app.py``: the operator points
``DARKFLEET_DATA`` at a snapshot directory (and optionally ``DARKFLEET_AIS_DIR`` at the
recorder cache) and nothing else needs configuring at show time.
"""

from __future__ import annotations

import json
import os
import re
import time
import zlib
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from darkfleet.core.tracks import Ping
from darkfleet.ingest.ais_tracks import parse_line

router = APIRouter(prefix="/api/live", tags=["live"])

# Region names become filenames, so only the snapshot job's own alphabet is allowed.
REGION_NAME = re.compile(r"^[A-Z_]+$")
SNAPSHOT_PREFIX = "live_"
# The summary endpoint hides these two arrays; the dedicated endpoints serve them.
SUMMARY_HIDDEN = frozenset({"events", "vessels"})
HEARTBEAT_SECONDS = 5.0
POSITIONS_PER_TICK = 200
GZIP_WBITS = 16 + zlib.MAX_WBITS  # 16 selects the gzip container instead of raw zlib


def data_dir() -> Path:
    """Resolve the snapshot directory per request so tests can swap it via the environment."""
    return Path(os.environ.get("DARKFLEET_DATA", "data/snapshot"))


@lru_cache(maxsize=16)
def _load_snapshot(path: str, mtime_ns: int) -> dict[str, Any]:
    """Parse a live file once per modification; the mtime key makes rebuilds visible."""
    del mtime_ns  # cache key only: a rewrite changes the key and therefore reparses
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"live snapshot must be an object, got {type(payload).__name__}")
    return payload


def reset_cache() -> None:
    """Drop parsed snapshots; tests change DARKFLEET_DATA between cases."""
    _load_snapshot.cache_clear()


def _checked_region(region: str) -> str:
    """Reject anything outside ``[A-Z_]+`` before it can be joined into a path."""
    if REGION_NAME.fullmatch(region) is None:
        raise HTTPException(status_code=400, detail="invalid region name")
    return region


def _payload_for(region: str) -> dict[str, Any]:
    """Load one region's snapshot, answering 404 JSON when no file exists for it."""
    _checked_region(region)
    path = data_dir() / f"{SNAPSHOT_PREFIX}{region}.json"
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"unknown region: {region}") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown region: {region}")
    return _load_snapshot(str(path), mtime_ns)


def _dicts(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Read an array of objects, ignoring a malformed entry instead of failing the response."""
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _severity(event: dict[str, Any]) -> float:
    """Sort by severity; a missing value ranks last rather than crashing the event feed."""
    value = event.get("severity")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _last_position(vessel: dict[str, Any]) -> tuple[float, float] | None:
    """Return the vessel's newest fix; a vessel without one cannot be placed in a bbox."""
    last = vessel.get("last")
    if not isinstance(last, dict):
        return None
    lat, lon = last.get("lat"), last.get("lon")
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return float(lat), float(lon)


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Parse ``minlon,minlat,maxlon,maxlat``; a malformed viewport is a client error."""
    parts = value.split(",")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="bbox must be minlon,minlat,maxlon,maxlat")
    try:
        minlon, minlat, maxlon, maxlat = (float(part) for part in parts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bbox values must be numbers") from exc
    return minlon, minlat, maxlon, maxlat


@router.get("/regions")
def list_regions() -> list[dict[str, Any]]:
    """List every readable live snapshot; one corrupt file must not blind the whole map."""
    regions: list[dict[str, Any]] = []
    try:
        paths = sorted(data_dir().glob(f"{SNAPSHOT_PREFIX}*.json"))
    except OSError:
        return regions
    for path in paths:
        region = path.stem.removeprefix(SNAPSHOT_PREFIX)
        if REGION_NAME.fullmatch(region) is None:
            continue
        try:
            payload = _load_snapshot(str(path), path.stat().st_mtime_ns)
        except (OSError, ValueError):
            continue
        regions.append(
            {
                "region": region,
                "generated_at": payload.get("generated_at"),
                "counts": payload.get("counts", {}),
            }
        )
    return regions


@router.get("/{region}/summary")
def get_summary(region: str) -> dict[str, Any]:
    """Everything except the two large arrays: header numbers, window and coverage."""
    payload = _payload_for(region)
    return {key: value for key, value in payload.items() if key not in SUMMARY_HIDDEN}


@router.get("/{region}/events")
def list_events(
    region: str,
    kind: str | None = None,
    limit: int = Query(default=100, ge=0),
) -> list[dict[str, Any]]:
    """Severity-ordered findings, filtered by kind, for the UI's event list."""
    events = _dicts(_payload_for(region), "events")
    if kind is not None:
        events = [event for event in events if event.get("kind") == kind]
    events.sort(key=_severity, reverse=True)
    return events[:limit]


@router.get("/{region}/vessels")
def list_vessels(region: str, bbox: str | None = None) -> list[dict[str, Any]]:
    """All vessels, or only those whose latest fix falls inside the requested viewport."""
    vessels = _dicts(_payload_for(region), "vessels")
    if bbox is None:
        return vessels
    minlon, minlat, maxlon, maxlat = _parse_bbox(bbox)
    selected: list[dict[str, Any]] = []
    for vessel in vessels:
        position = _last_position(vessel)
        if position is None:
            continue
        lat, lon = position
        if minlon <= lon <= maxlon and minlat <= lat <= maxlat:
            selected.append(vessel)
    return selected


@router.get("/{region}/vessel/{mmsi}")
def get_vessel(region: str, mmsi: int) -> dict[str, Any]:
    """One vessel plus its own events, so a marker click needs a single round trip."""
    payload = _payload_for(region)
    for vessel in _dicts(payload, "vessels"):
        if vessel.get("mmsi") != mmsi:
            continue
        events = [event for event in _dicts(payload, "events") if event.get("mmsi") == mmsi]
        events.sort(key=_severity, reverse=True)
        return {**vessel, "events": events}
    raise HTTPException(status_code=404, detail="vessel not found")


@router.get("/{region}/stream")
def stream_region(region: str) -> StreamingResponse:
    """SSE: a heartbeat every 5 s plus positions appended to the recorder since the last tick.

    This is the only long-lived call; history stays on the snapshot endpoints, so the stream
    never replays the past and a reconnecting browser cannot duplicate the map.
    """
    _payload_for(region)  # fail unknown regions before the response headers are sent
    return StreamingResponse(
        event_stream(region),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def event_stream(region: str) -> Iterator[str]:
    """Yield SSE frames forever; split out so tests can consume one tick without a server.

    The first tick emits only a heartbeat and primes the tail at the current end of the
    recorder file. Positions therefore mean "appended while this client was connected".
    """
    tail = _GzipTail()
    primed = False
    while True:
        yield _frame({"type": "heartbeat", "t": int(time.time())})
        root = os.environ.get("DARKFLEET_AIS_DIR")
        if root:
            positions = tail.poll(Path(root) / region, emit=primed)
            if positions:
                yield _frame({"type": "positions", "items": positions})
        primed = True
        time.sleep(HEARTBEAT_SECONDS)


def _frame(payload: dict[str, Any]) -> str:
    """Serialise one Server-Sent Event data frame; JSON keeps the browser client trivial."""
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _newest_recording(directory: Path) -> Path | None:
    """Pick the recording with the newest mtime: the recorder writes the current hour's file."""
    try:
        candidates = list(directory.glob("*.jsonl.gz"))
    except OSError:
        return None
    newest: Path | None = None
    newest_key: tuple[int, str] | None = None
    for candidate in candidates:
        try:
            key = (candidate.stat().st_mtime_ns, candidate.name)
        except OSError:
            continue
        if newest_key is None or key > newest_key:
            newest, newest_key = candidate, key
    return newest


def _decompressor() -> Any:
    """Fresh gzip decoder; the type is not exposed by zlib's stubs, hence ``Any``."""
    return zlib.decompressobj(GZIP_WBITS)


class _GzipTail:
    """Incrementally read one growing gzip recording, stopping cleanly at a truncated tail.

    The recorder appends through gzip, so at any instant the final deflate block can be
    half-written. Complete lines before that point are still useful, so a decode error is
    remembered and the next tick re-reads from byte zero instead of ending the stream.
    ``_emitted`` lets that resync skip lines the client already received.
    """

    def __init__(self) -> None:
        self._path: Path | None = None
        self._offset = 0
        self._pending = b""
        self._emitted = 0
        self._decompressor: Any = _decompressor()
        self._resync = False

    def poll(self, directory: Path, *, emit: bool) -> list[dict[str, Any]]:
        """Return up to ``POSITIONS_PER_TICK`` new positions from the newest recording file.

        ``emit=False`` consumes the file without returning anything, which primes the first
        tick: history belongs to the snapshot endpoints, not to the live tail.
        """
        path = _newest_recording(directory)
        if path is None:
            self._reset(None)
            return []
        if path != self._path:
            self._reset(path)
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if size < self._offset:
            self._reset(path)  # the recorder replaced/truncated the file under us
        reread = self._resync
        if reread:
            self._resync = False
            self._offset = 0
            self._pending = b""
            self._decompressor = _decompressor()
        if size <= self._offset:
            return []
        try:
            with path.open("rb") as handle:
                handle.seek(self._offset)
                raw = handle.read()
        except OSError:
            return []
        self._offset += len(raw)
        complete = self._consume(raw, reread=reread)
        if not emit:
            return []
        items: list[dict[str, Any]] = []
        for line in complete:
            item = _position_item(line)
            if item is not None:
                items.append(item)
        return items[-POSITIONS_PER_TICK:]

    def _reset(self, path: Path | None) -> None:
        """Start a new file (or no file) from the beginning with a clean decoder."""
        self._path = path
        self._offset = 0
        self._pending = b""
        self._emitted = 0
        self._decompressor = _decompressor()
        self._resync = False

    def _consume(self, raw: bytes, *, reread: bool) -> list[bytes]:
        """Decompress appended bytes and return the lines not yet handed to the stream.

        A normal incremental read produces only new lines. After a deflate error the next
        tick re-reads from byte zero, so ``reread`` makes us skip the prefix already sent.
        """
        out = bytearray()
        data = raw
        while data:
            if self._decompressor.eof:
                # The recorder starts a new gzip member when it reopens the file; gzip files
                # may legally contain several members, so continue with a fresh decoder.
                self._decompressor = _decompressor()
            try:
                out.extend(self._decompressor.decompress(data))
            except zlib.error:
                self._resync = True
                break
            data = self._decompressor.unused_data
        self._pending += bytes(out)
        parts = self._pending.split(b"\n")
        self._pending = parts[-1]  # a partial line waits for the next tick
        complete = parts[:-1]
        if reread:
            fresh = complete[self._emitted :]
            self._emitted = len(complete)
        else:
            fresh = complete
            self._emitted += len(complete)
        return fresh


def _position_item(line: bytes) -> dict[str, Any] | None:
    """Project one recorded line to the fields the live map needs; skip non-positions."""
    parsed = parse_line(line.decode("utf-8", errors="replace"))
    if not isinstance(parsed, Ping):
        return None
    return {
        "mmsi": parsed.mmsi,
        "lat": parsed.lat,
        "lon": parsed.lon,
        "sog": parsed.sog,
        "t": parsed.t.isoformat(),
    }
