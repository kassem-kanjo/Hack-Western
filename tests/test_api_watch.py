"""Contract tests for the watch router (#86): the snapshot file and its change-only SSE."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import watch
from darkfleet.api.app import app, reset_cache
from darkfleet.api.watch import event_stream

WATCH: dict[str, Any] = {
    "generated_at": "2026-11-21T19:20:00Z",
    "refresh_s": 300,
    "areas": [{"id": "BALTIC_WATCH", "name": "Baltic", "kind": "listening", "bbox": [9.5, 53.5, 30.5, 66.0]}],
    "ships": [],
    "alerts": [],
    "counts": {"ships": 0, "alerts": 0, "hidden_routine": 0},
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA, so stale parses must not leak."""
    reset_cache()
    watch.reset_cache()
    yield
    reset_cache()
    watch.reset_cache()


@pytest.fixture
def watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty snapshot directory, the state before the first watch build."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    return directory


def _write(directory: Path, generated_at: str) -> Path:
    path = directory / "watch.json"
    path.write_text(json.dumps({**WATCH, "generated_at": generated_at}), encoding="utf-8")
    return path


def test_missing_snapshot_is_503(watch_dir: Path) -> None:
    with TestClient(app) as client:
        response = client.get("/api/watch")

    assert response.status_code == 503
    assert response.json() == {"detail": "watch not built yet"}


def test_built_snapshot_is_served(watch_dir: Path) -> None:
    _write(watch_dir, "2026-11-21T19:20:00Z")

    with TestClient(app) as client:
        response = client.get("/api/watch")

    assert response.status_code == 200
    assert response.json() == WATCH


def test_stream_emits_update_after_the_file_changes(watch_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(watch_dir, "2026-11-21T19:20:00Z")
    monkeypatch.setattr(watch.time, "sleep", lambda _seconds: None)

    frames = event_stream()
    try:
        # The prime frame sets the mtime baseline without claiming an update.
        assert next(frames) == ": keepalive\n\n"

        before = path.stat().st_mtime_ns
        _write(watch_dir, "2026-11-21T19:25:00Z")
        # Force a distinct mtime even on a filesystem with coarse timestamps.
        os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))

        frame = next(frames)
    finally:
        frames.close()

    assert frame.startswith("event: update\ndata: ")
    body = json.loads(frame.split("data: ", 1)[1])
    assert body == {"generated_at": "2026-11-21T19:25:00Z"}
