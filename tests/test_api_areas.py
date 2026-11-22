"""Contract tests for the area dashboard router (#186).

The strip above the watch map must agree with the snapshot for shape and counts, and must read
its traffic rate from the recorder's hour files rather than from log text. The fixtures below
are deliberately small: one watch snapshot and two region recordings with known shape.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import areas, watch
from darkfleet.api.app import app, reset_cache

GENERATED_AT = "2026-11-21T12:00:00Z"

AREAS: list[dict[str, Any]] = [
    {
        "id": "BALTIC_WATCH",
        "name": "Baltic",
        "kind": "listening",
        "bbox": [9.5, 53.5, 30.5, 66.0],
    },
    {
        "id": "HORMUZ",
        "name": "Hormuz approaches",
        "kind": "listening",
        "bbox": [54.0, 22.0, 60.5, 27.5],
    },
    {
        "id": "HORMUZ_STRAIT",
        "name": "Strait of Hormuz",
        "kind": "watched",
        "bbox": [55.0, 24.0, 57.6, 27.2],
    },
    {
        "id": "RED_SEA",
        "name": "Red Sea",
        "kind": "listening",
        "bbox": [38.0, 11.5, 44.5, 20.5],
    },
]

SHIPS: list[dict[str, Any]] = [
    {"mmsi": 111111111, "lat": 55.0, "lon": 12.0},
    {"mmsi": 222222222, "lat": 60.0, "lon": 25.0},
    {"mmsi": 333333333, "lat": 26.0, "lon": 56.0},
    {"mmsi": 444444444, "lat": 26.5, "lon": 56.5},
    {"mmsi": 555555555, "lat": 0.0, "lon": 0.0},
]


def _alert(kind: str, area_id: str | None, t_start: str) -> dict[str, Any]:
    """Only the fields the area router reads; the rest of the contract is not exercised here."""
    return {"kind": kind, "area_id": area_id, "t_start": t_start}


ALERTS: list[dict[str, Any]] = [
    _alert("dark", "BALTIC_WATCH", "2026-11-21T11:00:00Z"),
    _alert("meeting", "BALTIC_WATCH", "2026-11-21T10:00:00Z"),
    # Older than the twelve-hour window: counted in the map's rail, not in this strip.
    _alert("dark", "BALTIC_WATCH", "2026-11-20T16:00:00Z"),
    _alert("dark", "HORMUZ_STRAIT", "2026-11-21T11:45:00Z"),
    _alert("spoof", "HORMUZ_STRAIT", "2026-11-21T11:30:00Z"),
    _alert("meeting", None, "2026-11-21T11:10:00Z"),
]

WATCH: dict[str, Any] = {
    "generated_at": GENERATED_AT,
    "refresh_s": 300,
    "areas": AREAS,
    "ships": SHIPS,
    "alerts": ALERTS,
    "counts": {"ships": len(SHIPS), "alerts": len(ALERTS), "hidden_routine": 0},
}


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test gets its own files; no parsed snapshot or hour may leak between cases.

    ``DATABASE_URL`` is cleared so the default path is the files, never a developer's live
    Tiger database; the Tiger tests below set it themselves after this fixture runs.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_cache()
    watch.reset_cache()
    areas.reset_cache()
    yield
    reset_cache()
    watch.reset_cache()
    areas.reset_cache()


@pytest.fixture
def snapshot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A snapshot directory pointed at by the app, with the one watch file."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    (directory / "watch.json").write_text(json.dumps(WATCH), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    return directory


@pytest.fixture
def ais_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty recorder root; tests create one folder per region they need."""
    directory = tmp_path / "ais"
    directory.mkdir()
    monkeypatch.setenv("DARKFLEET_AIS_DIR", str(directory))
    return directory


def _stamp(t: datetime) -> str:
    """aisstream's metadata spelling, including the nanoseconds the parser truncates."""
    return t.strftime("%Y-%m-%d %H:%M:%S") + f".{t.microsecond:06d}000 +0000 UTC"


def _position(mmsi: int, t: datetime, lat: float = 55.0, lon: float = 12.0) -> dict[str, Any]:
    """One parseable position report, enough for both ship counts and the message rate."""
    return {
        "MetaData": {"MMSI": mmsi, "time_utc": _stamp(t)},
        "MessageType": "PositionReport",
        "Message": {
            "PositionReport": {
                "Latitude": lat,
                "Longitude": lon,
                "Sog": 8.0,
                "Cog": 90.0,
                "TrueHeading": 90,
            }
        },
    }


def _write_hour(root: Path, region: str, hour: str, lines: list[dict[str, Any]]) -> Path:
    """Write one recorder hour file, named the way the recorder names it."""
    directory = root / region
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hour}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    return path


def _m(hour: int, minute: int, second: int = 0) -> datetime:
    """A UTC instant on the fixture day."""
    return datetime(2026, 11, 21, hour, minute, second, tzinfo=UTC)


def _by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in body["areas"]}


def test_missing_watch_is_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "snapshot").mkdir()
    monkeypatch.setenv("DARKFLEET_DATA", str(tmp_path / "snapshot"))

    with TestClient(app) as client:
        response = client.get("/api/areas/stats")

    assert response.status_code == 503
    assert response.json() == {"detail": "watch not built yet"}


def test_rows_follow_the_snapshot_shape_and_counts(snapshot_dir: Path, ais_dir: Path) -> None:
    with TestClient(app) as client:
        response = client.get("/api/areas/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"] == GENERATED_AT
    assert [row["id"] for row in body["areas"]] == [area["id"] for area in AREAS]

    rows = _by_id(body)
    baltic = rows["BALTIC_WATCH"]
    assert (baltic["name"], baltic["kind"], baltic["bbox"]) == ("Baltic", "listening", AREAS[0]["bbox"])
    assert baltic["ships_now"] == 2
    assert baltic["alerts_12h"] == {"dark": 1, "meeting": 1}

    hormuz = rows["HORMUZ"]
    assert hormuz["ships_now"] == 2
    assert hormuz["alerts_12h"] == {}

    strait = rows["HORMUZ_STRAIT"]
    assert strait["ships_now"] == 2
    # Fixed reading order: dark before spoof, not insertion order from the snapshot.
    assert strait["alerts_12h"] == {"dark": 1, "spoof": 1}


def test_traffic_rate_and_sparkline_come_from_the_recordings(snapshot_dir: Path, ais_dir: Path) -> None:
    _write_hour(
        ais_dir,
        "BALTIC_WATCH",
        "20261121T09",
        [_position(111111111, _m(9, 10)), _position(222222222, _m(9, 20))],
    )
    _write_hour(
        ais_dir,
        "BALTIC_WATCH",
        "20261121T10",
        [
            _position(111111111, _m(10, 4, 0)),
            _position(111111111, _m(10, 4, 10)),
            _position(222222222, _m(10, 4, 20)),
            _position(333333333, _m(10, 0)),
        ],
    )
    _write_hour(ais_dir, "HORMUZ", "20261121T10", [_position(444444444, _m(10, 4))])

    with TestClient(app) as client:
        rows = _by_id(client.get("/api/areas/stats").json())

    baltic = rows["BALTIC_WATCH"]
    # Four messages in the newest five minutes -> 0.8 per minute, three distinct hulls.
    assert baltic["msgs_per_min"] == 0.8
    assert baltic["sparkline"] == [0] * 10 + [2, 3]

    # A watched box has no folder of its own: it borrows the listening box it sits in.
    strait = rows["HORMUZ_STRAIT"]
    assert strait["msgs_per_min"] == 0.2
    assert strait["sparkline"] == [0] * 11 + [1]


def test_area_without_a_recording_is_zeroed(snapshot_dir: Path, ais_dir: Path) -> None:
    with TestClient(app) as client:
        rows = _by_id(client.get("/api/areas/stats").json())

    red_sea = rows["RED_SEA"]
    assert red_sea["msgs_per_min"] == 0.0
    assert red_sea["sparkline"] == [0] * 12
    assert red_sea["source"] == "files"


def _write_baltic_hour(ais_dir: Path) -> None:
    """Four messages in the newest five minutes, so the file path reports 0.8/min."""
    _write_hour(
        ais_dir,
        "BALTIC_WATCH",
        "20261121T10",
        [
            _position(111111111, _m(10, 4, 0)),
            _position(111111111, _m(10, 4, 10)),
            _position(222222222, _m(10, 4, 20)),
            _position(333333333, _m(10, 0)),
        ],
    )


def test_unreachable_tiger_falls_back_to_the_files(
    snapshot_dir: Path, ais_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_baltic_hour(ais_dir)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/never")

    def unreachable() -> None:
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(areas, "_tiger_repo", unreachable)

    with TestClient(app) as client:
        rows = _by_id(client.get("/api/areas/stats").json())

    baltic = rows["BALTIC_WATCH"]
    assert baltic["source"] == "files"
    assert baltic["msgs_per_min"] == 0.8
    assert baltic["sparkline"] == [0] * 11 + [3]


class _FakeTiger:
    """Minimal repo: one region has minute buckets, the rest are empty."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, datetime, datetime]] = []
        self.closed = False

    def minutely(self, region: str, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
        self.queries.append((region, start, end))
        if region != "BALTIC_WATCH":
            return []
        last_full = end - timedelta(minutes=1)
        return [(last_full - timedelta(minutes=2), 3), (last_full, 7)]

    def close(self) -> None:
        self.closed = True


def test_tiger_supplies_the_sparkline_and_rate(
    snapshot_dir: Path, ais_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The files describe a different rate on purpose: the response must come from Tiger.
    _write_baltic_hour(ais_dir)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/never")
    fake = _FakeTiger()
    monkeypatch.setattr(areas, "_tiger_repo", lambda: fake)

    with TestClient(app) as client:
        rows = _by_id(client.get("/api/areas/stats").json())
        first_queries = len(fake.queries)
        rows_again = _by_id(client.get("/api/areas/stats").json())

    baltic = rows["BALTIC_WATCH"]
    assert baltic["source"] == "tiger"
    # 10-minute average ending at the newest minute with data: (3 + 7) / 10.
    assert baltic["msgs_per_min"] == 1.0
    assert len(baltic["sparkline"]) == 60
    assert baltic["sparkline"][-1] == 7
    assert baltic["sparkline"][-3] == 3

    # No rows at all for a region in Tiger means the files answer for it (the sink may not
    # have caught up on that region yet), never a zero-traffic reading.
    assert rows["HORMUZ"]["source"] == "files"
    assert rows_again["BALTIC_WATCH"]["source"] == "tiger"

    # Readings are cached for 20 s, so the second request adds no queries.
    assert len(fake.queries) == first_queries
    assert "BALTIC_WATCH" in {region for region, _, _ in fake.queries}


class _EmptyTiger:
    """A reachable repo whose regions have no rows at all."""

    def minutely(self, region: str, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
        return []

    def close(self) -> None:
        return None


def test_tiger_with_no_rows_for_any_name_falls_back_to_files(
    snapshot_dir: Path, ais_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_baltic_hour(ais_dir)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/never")
    monkeypatch.setattr(areas, "_tiger_repo", lambda: _EmptyTiger())

    with TestClient(app) as client:
        rows = _by_id(client.get("/api/areas/stats").json())

    assert rows["BALTIC_WATCH"]["source"] == "files"
    assert rows["BALTIC_WATCH"]["msgs_per_min"] == 0.8


class _CountingFailingTimescale:
    """Stands in for TimescaleRepo: every connect fails, and the attempts are counted."""

    attempts: int = 0
    kwargs: dict[str, object] = {}

    def __init__(self, url: str, **kwargs: object) -> None:
        type(self).attempts += 1
        type(self).kwargs = dict(kwargs)
        raise ConnectionError("connect timed out")


def test_failed_tiger_connect_is_remembered_for_a_minute(
    snapshot_dir: Path, ais_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/never")
    monkeypatch.setattr(areas, "TimescaleRepo", _CountingFailingTimescale)
    _CountingFailingTimescale.attempts = 0

    with TestClient(app) as client:
        response = client.get("/api/areas/stats")
        client.get("/api/areas/stats")

    # One connect attempt for the whole strip, not one per area per request.
    assert response.status_code == 200
    assert {row["source"] for row in response.json()["areas"]} == {"files"}
    assert _CountingFailingTimescale.attempts == 1
    # The API never runs schema DDL on its connection.
    assert _CountingFailingTimescale.kwargs.get("apply_schema") is False
