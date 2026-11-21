"""H-1/M-3 regressions: SSE streams must be async, capped, and cancel on disconnect.

The live-region contract tests live in ``tests/api/test_live.py``; this file covers the two
CP3 security findings that only apply to long-lived streams and the gzip tail.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from darkfleet.api import live
from darkfleet.api.live import event_stream

REGION = "TEST"
GENERATED_AT = "2026-11-21T06:00:00Z"
LIVE = {
    "region": REGION,
    "generated_at": GENERATED_AT,
    "counts": {"pings": 1, "vessels": 0, "silences": 0, "jumps": 0},
    "events": [],
    "vessels": [],
}


class FakeRequest:
    """Stands in for ``Request``: answers ``is_disconnected`` from a scripted list."""

    def __init__(self, answers: list[bool]) -> None:
        self._answers = answers
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        if not self._answers:
            return False
        return self._answers.pop(0)


@pytest.fixture(autouse=True)
def _reset_slots() -> Iterator[None]:
    live.reset_stream_slots()
    yield
    live.reset_stream_slots()


@pytest.fixture
def live_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "snapshot"
    directory.mkdir()
    (directory / f"live_{REGION}.json").write_text(json.dumps(LIVE), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    monkeypatch.delenv("DARKFLEET_AIS_DIR", raising=False)
    return directory


def test_disconnect_ends_the_generator_before_the_next_sleep(
    live_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A huge heartbeat makes a blocking/looping stream fail loudly instead of passing slowly.
    monkeypatch.setattr(live, "HEARTBEAT_SECONDS", 3600.0)
    request = FakeRequest([False, True])

    async def scenario() -> list[str]:
        frames: list[str] = []
        async for frame in event_stream(REGION, request):  # type: ignore[arg-type]
            frames.append(frame)
        return frames

    frames = asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))

    assert len(frames) == 1  # one heartbeat, then the disconnect check stopped the loop
    assert json.loads(frames[0].removeprefix("data: ").strip())["type"] == "heartbeat"
    assert request.calls == 2


def test_stream_slots_are_capped(live_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "MAX_CONCURRENT_STREAMS", 1)

    async def scenario() -> tuple[Any, HTTPException]:
        first = await live.stream_region(REGION, FakeRequest([]))  # type: ignore[arg-type]
        try:
            with pytest.raises(HTTPException) as caught:
                await live.stream_region(REGION, FakeRequest([]))  # type: ignore[arg-type]
            return first, caught.value
        finally:
            body = first.body_iterator
            await body.aclose()  # type: ignore[attr-defined]

    first, error = asyncio.run(scenario())

    assert first.status_code == 200
    assert error.status_code == 503
    assert error.detail == "too many live streams"
    assert live.active_streams() == 0


def test_the_slot_is_released_when_the_response_closes(
    live_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "HEARTBEAT_SECONDS", 0.0)

    async def scenario() -> str:
        response = await live.stream_region(REGION, FakeRequest([]))  # type: ignore[arg-type]
        body = response.body_iterator
        frame = await anext(body)  # type: ignore[call-overload]
        assert live.active_streams() == 1
        await body.aclose()  # type: ignore[attr-defined]
        return frame

    frame = asyncio.run(scenario())

    assert frame.startswith("data: ")
    assert live.active_streams() == 0


def test_gzip_tail_reads_at_most_one_chunk_per_tick(tmp_path: Path) -> None:
    directory = tmp_path / "REC"
    directory.mkdir()
    recording = directory / "20261121T05.jsonl.gz"
    # Incompressible, so the compressed file itself is larger than one tick's budget.
    recording.write_bytes(gzip.compress(os.urandom(3 << 20)))
    tail = live._GzipTail()

    tail.poll(directory, emit=False)
    first_offset = tail._offset
    tail.poll(directory, emit=False)

    assert 0 < first_offset <= live.MAX_TICK_READ_BYTES
    assert first_offset < tail._offset <= 2 * live.MAX_TICK_READ_BYTES


def test_gzip_tail_resync_does_not_repeat_delivered_lines(tmp_path: Path) -> None:
    directory = tmp_path / "REC"
    directory.mkdir()
    recording = directory / "20261121T05.jsonl.gz"
    line = {
        "MetaData": {"MMSI": 276462000, "time_utc": "2026-11-21 05:00:00.000000000 +0000 UTC"},
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {"Latitude": 55.5, "Longitude": 12.5, "Sog": 7.5}},
    }
    recording.write_bytes(gzip.compress(json.dumps(line).encode() + b"\n") + b"\x00garbage")
    tail = live._GzipTail()

    first = tail.poll(directory, emit=True)
    second = tail.poll(directory, emit=True)

    assert [item["mmsi"] for item in first] == [276462000]
    assert second == []  # the re-read skipped the line the client already received


def test_gzip_tail_resyncs_at_most_once_per_file(tmp_path: Path) -> None:
    directory = tmp_path / "REC"
    directory.mkdir()
    recording = directory / "20261121T05.jsonl.gz"
    recording.write_bytes(gzip.compress(b'{"line":1}\n') + b"\x00not-a-gzip-member")
    tail = live._GzipTail()

    tail.poll(directory, emit=False)
    assert tail._resync_target is not None  # one re-read is planned

    tail.poll(directory, emit=False)
    assert tail._resync_target is None  # the second error does not loop back to byte zero
    after_resync = tail._offset
    assert after_resync > 0

    tail.poll(directory, emit=False)
    assert tail._offset == after_resync  # nothing left, and still not restarting from 0
