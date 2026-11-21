"""Watch-screen API (issue #86): one global snapshot plus a change-only SSE stream.

The screen is different from the live-region router: there is one file, ``watch.json``, rebuilt
on a fixed cadence by ``scripts/build_watch.py``, and its clients care only that a *new* build
landed. So the stream never streams positions - it emits an ``update`` event carrying the new
``generated_at`` whenever the file's mtime changes, and the browser refetches.

Read-only and file-backed like the rest of ``api/``: ``DARKFLEET_DATA`` points at the snapshot
folder (the same setting ``api/live.py`` uses), and a missing file is an explicit 503 rather
than an empty contract-shaped object, so the UI can say "not built yet" honestly.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["watch"])

WATCH_FILE = "watch.json"
POLL_SECONDS = 5.0
KEEPALIVE_SECONDS = 15.0
NOT_BUILT = "watch not built yet"


def data_dir() -> Path:
    """Resolve the snapshot directory per request, matching ``api/live.py``."""
    return Path(os.environ.get("DARKFLEET_DATA", "data/snapshot"))


def watch_path() -> Path:
    """The one file this router serves."""
    return data_dir() / WATCH_FILE


@lru_cache(maxsize=8)
def _load_watch(path: str, mtime_ns: int) -> dict[str, Any]:
    """Parse watch.json once per modification; the mtime key makes rebuilds visible."""
    del mtime_ns  # cache key only: a rewrite changes the key and therefore reparses
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"watch snapshot must be an object, got {type(payload).__name__}")
    return payload


def reset_cache() -> None:
    """Drop the parsed snapshot; tests swap DARKFLEET_DATA between cases."""
    _load_watch.cache_clear()


def _mtime_ns(path: Path) -> int | None:
    """File mtime in nanoseconds, or None when watch.json has not been built yet."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


@router.get("/api/watch")
def get_watch() -> dict[str, Any]:
    """The whole watch snapshot, or a 503 JSON body when the build has not run."""
    path = watch_path()
    mtime_ns = _mtime_ns(path)
    if mtime_ns is None:
        raise HTTPException(status_code=503, detail=NOT_BUILT)
    try:
        return _load_watch(str(path), mtime_ns)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=NOT_BUILT) from exc


# Async on purpose: a sync generator with time.sleep pins one threadpool thread per open
# stream, so a handful of idle tabs could starve the API (CP3 security review, H-1).
MAX_STREAMS = 50
_open_streams = 0


@router.get("/api/watch/stream")
async def stream_watch(request: Request) -> StreamingResponse:
    """SSE: an ``update`` event whenever watch.json changes, keepalives between builds."""
    if _open_streams >= MAX_STREAMS:
        raise HTTPException(status_code=503, detail="too many watch streams")
    return StreamingResponse(
        _counted(event_stream(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _counted(frames: AsyncIterator[str]) -> AsyncIterator[str]:
    """Track open streams so the cap above holds; always decrements, even on disconnect."""
    global _open_streams
    _open_streams += 1
    try:
        async for frame in frames:
            yield frame
    finally:
        _open_streams -= 1


async def event_stream(request: Request | None = None) -> AsyncIterator[str]:
    """Poll the snapshot's mtime every 5 s and emit only on change.

    Split out so a test can consume one tick without running a server. The first event is sent
    only after the file has actually changed: a client that just fetched ``/api/watch`` does not
    need an immediate second copy.
    """
    path = watch_path()
    last_mtime = _mtime_ns(path)
    last_sent = time.monotonic()
    # Prime the connection (and, in tests, the mtime baseline) before the first poll.
    yield ": keepalive\n\n"
    while True:
        await asyncio.sleep(POLL_SECONDS)
        if request is not None and await request.is_disconnected():
            return
        current = _mtime_ns(path)
        if current is not None and current != last_mtime:
            last_mtime = current
            last_sent = time.monotonic()
            yield _update_frame(path)
            continue
        if time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
            last_sent = time.monotonic()
            yield ": keepalive\n\n"


def _update_frame(path: Path) -> str:
    """One ``event: update`` frame carrying only the new build's timestamp."""
    generated_at = None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            generated_at = payload.get("generated_at")
    except (OSError, ValueError):
        generated_at = None
    body = json.dumps({"generated_at": generated_at}, separators=(",", ":"))
    return f"event: update\ndata: {body}\n\n"
