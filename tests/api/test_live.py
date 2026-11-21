"""Contract tests for the live-region router (#80): snapshots plus one SSE tick."""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import live
from darkfleet.api.app import app, reset_cache
from darkfleet.api.live import event_stream

REGION = "TEST"
GENERATED_AT = "2026-11-21T06:00:00Z"
COUNTS = {"pings": 42, "vessels": 2, "silences": 2, "jumps": 1}


def _event(kind: str, mmsi: int, severity: float) -> dict[str, Any]:
    """Build an event with the fields the UI reads; only severity and mmsi vary here."""
    return {
        "kind": kind,
        "mmsi": mmsi,
        "start": "2026-11-21T04:00:00Z",
        "end": "2026-11-21T05:00:00Z",
        "lat": 55.0,
        "lon": 12.0,
        "severity": severity,
        "explanation": f"{kind} for {mmsi}",
        "evidence": {},
    }


EVENTS = [_event("silence", 111, 0.4), _event("jump", 222, 0.9), _event("silence", 333, 0.7)]
VESSELS = [
    {
        "mmsi": 111,
        "name": "ALPHA",
        "imo": 9000001,
        "ship_type": 80,
        "last": {"t": "2026-11-21T05:00:00Z", "lat": 55.0, "lon": 12.0, "sog": 8.5},
    },
    {
        "mmsi": 222,
        "name": "BRAVO",
        "imo": 9000002,
        "ship_type": 70,
        "last": {"t": "2026-11-21T05:00:00Z", "lat": 58.0, "lon": 15.0, "sog": 0.0},
    },
]
LIVE = {
    "region": REGION,
    "generated_at": GENERATED_AT,
    "window": {"start": "2026-11-21T04:00:00Z", "end": "2026-11-21T05:00:00Z"},
    "counts": COUNTS,
    "events": EVENTS,
    "vessels": VESSELS,
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA, so stale parses must not leak."""
    reset_cache()
    live.reset_cache()
    yield
    reset_cache()
    live.reset_cache()


@pytest.fixture
def live_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a one-region snapshot directory and point the app at it."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    (directory / f"live_{REGION}.json").write_text(json.dumps(LIVE), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    monkeypatch.delenv("DARKFLEET_AIS_DIR", raising=False)
    return directory


@pytest.fixture
def client(live_dir: Path) -> Iterator[TestClient]:
    """Use the context manager so startup/shutdown hooks run as in production."""
    with TestClient(app) as test_client:
        yield test_client


def test_regions_lists_every_live_file(client: TestClient) -> None:
    response = client.get("/api/live/regions")
    assert response.status_code == 200
    assert response.json() == [
        {"region": REGION, "generated_at": GENERATED_AT, "counts": COUNTS},
    ]


def test_summary_omits_events_and_vessels(client: TestClient) -> None:
    body = client.get(f"/api/live/{REGION}/summary").json()
    assert body == {key: value for key, value in LIVE.items() if key not in {"events", "vessels"}}
    assert "events" not in body
    assert "vessels" not in body


def test_events_sorted_by_severity_and_filtered(client: TestClient) -> None:
    everything = client.get(f"/api/live/{REGION}/events").json()
    assert [event["severity"] for event in everything] == [0.9, 0.7, 0.4]

    silences = client.get(f"/api/live/{REGION}/events", params={"kind": "silence"}).json()
    assert [event["mmsi"] for event in silences] == [333, 111]

    limited = client.get(f"/api/live/{REGION}/events", params={"limit": 1}).json()
    assert [event["mmsi"] for event in limited] == [222]


def test_vessels_are_filtered_by_bbox(client: TestClient) -> None:
    everything = client.get(f"/api/live/{REGION}/vessels").json()
    assert [vessel["mmsi"] for vessel in everything] == [111, 222]

    inside = client.get(f"/api/live/{REGION}/vessels", params={"bbox": "10,50,13,56"}).json()
    assert [vessel["mmsi"] for vessel in inside] == [111]

    outside = client.get(f"/api/live/{REGION}/vessels", params={"bbox": "0,0,1,1"}).json()
    assert outside == []


def test_vessel_detail_includes_its_events(client: TestClient) -> None:
    body = client.get(f"/api/live/{REGION}/vessel/111").json()
    assert body["mmsi"] == 111
    assert body["name"] == "ALPHA"
    assert [event["kind"] for event in body["events"]] == ["silence"]

    missing = client.get(f"/api/live/{REGION}/vessel/999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "vessel not found"}


def test_bad_region_name_is_rejected(client: TestClient) -> None:
    response = client.get("/api/live/test/summary")
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid region name"}


def test_unknown_region_is_404_json(client: TestClient) -> None:
    response = client.get("/api/live/UNKNOWN/summary")
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown region: UNKNOWN"}


def _decode(frame: str) -> dict[str, Any]:
    return json.loads(frame.removeprefix("data: ").strip())


async def _one_tick(region: str) -> str:
    """Consume the first frame of a fresh stream, then close it like a disconnecting client."""
    frames = event_stream(region)
    try:
        return await anext(frames)
    finally:
        await frames.aclose()


def test_stream_starts_with_a_heartbeat(live_dir: Path) -> None:
    frame = asyncio.run(_one_tick(REGION))

    assert frame.startswith("data: ")
    payload = _decode(frame)
    assert payload["type"] == "heartbeat"
    assert isinstance(payload["t"], int)


def test_stream_emits_positions_appended_since_the_previous_tick(
    live_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = live_dir.parent / "ais" / REGION
    cache.mkdir(parents=True)
    recording = cache / "20261121T05.jsonl.gz"
    first = {
        "MetaData": {"MMSI": 276462000, "time_utc": "2026-11-21 05:00:00.000000000 +0000 UTC"},
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {"Latitude": 55.5, "Longitude": 12.5, "Sog": 7.5}},
    }
    with gzip.open(recording, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(first) + "\n")

    second = {
        "MetaData": {"MMSI": 305773000, "time_utc": "2026-11-21 05:00:10.000000000 +0000 UTC"},
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {"Latitude": 59.0, "Longitude": 10.0, "Sog": 0.0}},
    }

    monkeypatch.setenv("DARKFLEET_AIS_DIR", str(live_dir.parent / "ais"))
    monkeypatch.setattr(live, "HEARTBEAT_SECONDS", 0.0)

    async def scenario() -> dict[str, Any]:
        frames = event_stream(REGION)
        try:
            # First frame is the heartbeat; the next iteration primes the tail at end-of-file,
            # swallowing the line that existed before we connected.
            assert _decode(await anext(frames))["type"] == "heartbeat"
            assert _decode(await anext(frames))["type"] == "heartbeat"

            with gzip.open(recording, "at", encoding="utf-8") as handle:
                handle.write(json.dumps(second) + "\n")

            return _decode(await anext(frames))
        finally:
            await frames.aclose()

    positions_frame = asyncio.run(scenario())

    assert positions_frame["type"] == "positions"
    assert positions_frame["items"] == [
        {"mmsi": 305773000, "lat": 59.0, "lon": 10.0, "sog": 0.0, "t": "2026-11-21T05:00:10+00:00"},
    ]
