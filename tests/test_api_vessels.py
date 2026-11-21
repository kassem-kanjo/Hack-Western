"""Contract tests for the vessel dossier (#182): watch + live + recording joined per hull."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import live, watch
from darkfleet.api.app import app, reset_cache
from darkfleet.api.vessels import reset_cache as reset_vessel_cache

MMSI = 636123456  # MID 636 = Liberia, the contract's "cannot be a real MMSI" test case is elsewhere
GENERATED_AT = "2026-11-21T19:20:00Z"


def _stamp(when: str) -> str:
    """The recorder's ``YYYY-MM-DD HH:MM:SS`` spelling of a UTC timestamp."""
    return datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _position(
    mmsi: int,
    when: str,
    lat: float,
    lon: float,
    sog: float = 8.0,
    cog: float = 90.0,
) -> str:
    """One raw recorder line (aisstream shape) with a position report."""
    stamp = _stamp(when)
    return json.dumps(
        {
            "MetaData": {"MMSI": mmsi, "time_utc": f"{stamp} +0000 UTC"},
            "MessageType": "PositionReport",
            "Message": {
                "PositionReport": {
                    "UserID": mmsi,
                    "Latitude": lat,
                    "Longitude": lon,
                    "Sog": sog,
                    "Cog": cog,
                    "TrueHeading": 90,
                    "NavigationalStatus": 0,
                }
            },
        }
    )


def _static(mmsi: int, when: str) -> str:
    """One raw recorder line carrying the static/voyage fields only a recording has."""
    stamp = _stamp(when)
    return json.dumps(
        {
            "MetaData": {"MMSI": mmsi, "time_utc": f"{stamp} +0000 UTC"},
            "MessageType": "ShipStaticData",
            "Message": {
                "ShipStaticData": {
                    "UserID": mmsi,
                    "ImoNumber": 9427366,
                    "Name": "GESTIA",
                    "CallSign": "9HA1234",
                    "Type": 80,
                    "Dimension": {"A": 120, "B": 60, "C": 15, "D": 15},
                    "Destination": "ROTTERDAM",
                }
            },
        }
    )


WATCH: dict[str, Any] = {
    "generated_at": GENERATED_AT,
    "refresh_s": 300,
    "areas": [{"id": "BALTIC_WATCH", "name": "Baltic", "kind": "listening", "bbox": [9.5, 53.5, 30.5, 66.0]}],
    "ships": [
        {
            "mmsi": MMSI,
            "valid": True,
            "name": "GESTIA",
            "lat": 54.0,
            "lon": 10.0,
            "t": "2026-11-21T19:00:00Z",
            "sog": 0.0,
            "class": "tanker",
            "watchlist": "sanctioned",
            "alert_ids": [f"dark-{MMSI}-1790000000", "meeting-111111111-1790000000"],
        }
    ],
    "alerts": [
        {
            "id": f"dark-{MMSI}-1790000000",
            "kind": "dark",
            "status": "ended",
            "mmsi": MMSI,
            "name": "GESTIA",
            "area_id": "BALTIC_WATCH",
            "severity": 0.9,
            "label": "Went dark",
            "t_start": "2026-11-21T18:00:00Z",
            "t_end": "2026-11-21T18:40:00Z",
            "last": {"lat": 54.2, "lon": 10.2, "t": "2026-11-21T18:00:00Z", "sog": 9.0, "cog": 90.0},
            "next": None,
            "speed_cap_kn": 17.25,
            "contacts": [],
            "trail": [
                [10.3, 54.3, "2026-11-21T17:30:00Z"],
                [10.25, 54.25, "2026-11-21T17:45:00Z"],
            ],
            "evidence": {},
        },
        {
            "id": "meeting-111111111-1790000000",
            "kind": "meeting",
            "status": "ended",
            "mmsi": 111111111,
            "name": "OTHER",
            "area_id": "BALTIC_WATCH",
            "severity": 0.5,
            "label": "Ships met",
            "t_start": "2026-11-21T16:00:00Z",
            "t_end": None,
            "last": {"lat": 54.1, "lon": 10.1, "t": "2026-11-21T16:00:00Z", "sog": 0.0, "cog": None},
            "next": None,
            "speed_cap_kn": 12.0,
            "contacts": [
                {
                    "mmsi": MMSI,
                    "name": "GESTIA",
                    "watchlist": "sanctioned",
                    "lat": 54.15,
                    "lon": 10.15,
                    "t": "2026-11-21T15:50:00Z",
                    "min_dist_km": 1.2,
                    "stopped": True,
                    "could_meet": True,
                }
            ],
            "trail": [],
            "evidence": {},
        },
        {
            "id": "dark-999999999-1790000000",
            "kind": "dark",
            "status": "ongoing",
            "mmsi": 999999999,
            "name": "UNRELATED",
            "area_id": "BALTIC_WATCH",
            "severity": 0.3,
            "label": "Went dark",
            "t_start": "2026-11-21T17:00:00Z",
            "t_end": None,
            "last": {"lat": 55.0, "lon": 12.0, "t": "2026-11-21T17:00:00Z", "sog": 1.0, "cog": 0.0},
            "next": None,
            "speed_cap_kn": 14.0,
            "contacts": [],
            "trail": [],
            "evidence": {},
        },
    ],
    "counts": {"ships": 1, "alerts": 3, "hidden_routine": 4},
}

LIVE: dict[str, Any] = {
    "region": "BALTIC_WATCH",
    "generated_at": "2026-11-21T19:15:00Z",
    "window": {"start": "2026-11-21T07:15:00Z", "end": "2026-11-21T19:15:00Z"},
    "counts": {"pings": 120, "vessels": 1},
    "vessels": [
        {
            "mmsi": MMSI,
            "name": "GESTIA",
            "imo": 9427366,
            "ship_type": 80,
            "class": "tanker",
            "last": {"t": "2026-11-21T19:10:00Z", "lat": 54.85, "lon": 10.75, "sog": 9.1},
        }
    ],
    "watchlist": {
        "alerts": [
            {
                "mmsi": MMSI,
                "imo": 9427366,
                "name": "GESTIA",
                "category": "sanctioned",
                "strength": "imo",
                "entry": {
                    "caption": "GESTIA (IMO 9427366)",
                    "imo": 9427366,
                    "risk": ["sanction"],
                    "url": "https://www.opensanctions.org/entities/NK-abc/",
                    "datasets": ["us_ofac_sdn"],
                },
            }
        ],
        "info": [],
    },
}


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA and recorder root."""
    reset_cache()
    watch.reset_cache()
    live.reset_cache()
    reset_vessel_cache()
    yield
    reset_cache()
    watch.reset_cache()
    live.reset_cache()
    reset_vessel_cache()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Snapshot folder plus an empty recorder root, both redirected to the temp dir."""
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    ais = tmp_path / "ais"
    ais.mkdir()
    monkeypatch.setenv("DARKFLEET_DATA", str(snapshot))
    monkeypatch.setenv("DARKFLEET_AIS_DIR", str(ais))
    return snapshot


def _write_json(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_recording(root: Path, lines: list[str], region: str = "BALTIC_WATCH") -> Path:
    """Write one gzip hour file in the recorder's alphabet."""
    directory = root / region
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "20261121T19.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def test_unknown_mmsi_is_404(data_dir: Path) -> None:
    _write_json(data_dir, "watch.json", WATCH)

    with TestClient(app) as client:
        response = client.get("/api/vessel/123456789")

    assert response.status_code == 404
    assert response.json() == {"detail": "vessel not found"}


def test_dossier_joins_watch_live_and_recording(data_dir: Path) -> None:
    _write_json(data_dir, "watch.json", WATCH)
    _write_json(data_dir, "live_BALTIC_WATCH.json", LIVE)
    ais = Path(data_dir).parent / "ais"
    _write_recording(
        ais,
        [
            _static(MMSI, "2026-11-21T18:55:00Z"),
            _position(MMSI, "2026-11-21T19:00:00Z", 54.55, 10.55, sog=8.4, cog=91.0),
            _position(MMSI, "2026-11-21T19:10:00Z", 54.85, 10.75, sog=9.1, cog=93.0),
        ],
    )

    with TestClient(app) as client:
        response = client.get(f"/api/vessel/{MMSI}")

    assert response.status_code == 200
    body = response.json()
    assert body["mmsi"] == MMSI
    assert body["name"] == "GESTIA"
    assert body["imo"] == 9427366
    assert body["callsign"] == "9HA1234"
    assert body["type"] == "Tanker"
    assert body["class"] == "tanker"
    assert body["flag"] == "Liberia"
    assert body["length_m"] == 180.0
    assert body["destination"] == "ROTTERDAM"

    # The recording's newest fix beats the (older) live row and the watch ship snapshot.
    assert body["last"] == {
        "lat": 54.85,
        "lon": 10.75,
        "t": "2026-11-21T19:10:00Z",
        "sog": 9.1,
        "cog": 93.0,
    }

    # Track is the ship's own fixes in the 12 h window, in time order, as [lon, lat, t].
    assert body["track"] == [
        [10.55, 54.55, "2026-11-21T19:00:00Z"],
        [10.75, 54.85, "2026-11-21T19:10:00Z"],
    ]

    assert body["alerts"] == [f"dark-{MMSI}-1790000000", "meeting-111111111-1790000000"]
    assert body["stats"] == {
        "pings_12h": 2,
        "first_seen": "2026-11-21T19:00:00Z",
        "last_seen": "2026-11-21T19:10:00Z",
    }
    assert body["watchlist"] == {
        "category": "sanctioned",
        "list_name": "GESTIA (IMO 9427366)",
        "datasets": ["us_ofac_sdn"],
        "url": "https://www.opensanctions.org/entities/NK-abc/",
    }


def test_watchlist_falls_back_to_the_watch_ship_without_a_citation(data_dir: Path) -> None:
    _write_json(data_dir, "watch.json", WATCH)

    with TestClient(app) as client:
        body = client.get(f"/api/vessel/{MMSI}").json()

    assert body["watchlist"] == {
        "category": "sanctioned",
        "list_name": None,
        "datasets": [],
        "url": None,
    }
    # No live row and no recording: the watch file still supplies the last fix and alerts.
    assert body["last"]["lat"] == 54.0
    assert body["track"] == [
        [10.3, 54.3, "2026-11-21T17:30:00Z"],
        [10.25, 54.25, "2026-11-21T17:45:00Z"],
    ]
    assert body["stats"]["pings_12h"] == 0


def test_recording_alone_is_enough_for_a_dossier(data_dir: Path) -> None:
    ais = Path(data_dir).parent / "ais"
    _write_recording(
        ais,
        [
            _static(MMSI, "2026-11-21T18:55:00Z"),
            _position(MMSI, "2026-11-21T19:00:00Z", 54.55, 10.55),
        ],
    )

    with TestClient(app) as client:
        response = client.get(f"/api/vessel/{MMSI}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "GESTIA"
    assert body["callsign"] == "9HA1234"
    assert body["watchlist"] is None
    assert body["alerts"] == []
    assert body["track"] == [[10.55, 54.55, "2026-11-21T19:00:00Z"]]
    assert body["stats"]["pings_12h"] == 1


def test_track_is_decimated_to_two_hundred_points(data_dir: Path) -> None:
    _write_json(data_dir, "watch.json", WATCH)
    start = datetime(2026, 11, 21, 7, 20, tzinfo=UTC)
    lines = [
        _position(MMSI, (start + timedelta(minutes=index)).isoformat(), 54.0 + index / 10_000, 10.0)
        for index in range(300)
    ]
    ais = Path(data_dir).parent / "ais"
    _write_recording(ais, lines)

    with TestClient(app) as client:
        body = client.get(f"/api/vessel/{MMSI}").json()

    track = body["track"]
    assert len(track) == 200
    assert track[0] == [10.0, 54.0, (start.isoformat(timespec="seconds")).replace("+00:00", "Z")]
    last_stamp = (start + timedelta(minutes=299)).isoformat(timespec="seconds").replace("+00:00", "Z")
    assert track[-1] == [10.0, 54.0299, last_stamp]
    assert [point[2] for point in track] == sorted(point[2] for point in track)
    assert body["stats"]["pings_12h"] == 300


def test_fixes_outside_the_twelve_hour_window_are_not_drawn(data_dir: Path) -> None:
    _write_json(data_dir, "watch.json", WATCH)
    ais = Path(data_dir).parent / "ais"
    _write_recording(
        ais,
        [
            _position(MMSI, "2026-11-21T02:00:00Z", 50.0, 5.0),
            _position(MMSI, "2026-11-21T19:00:00Z", 54.55, 10.55),
        ],
    )

    with TestClient(app) as client:
        body = client.get(f"/api/vessel/{MMSI}").json()

    assert body["track"] == [[10.55, 54.55, "2026-11-21T19:00:00Z"]]
    assert body["stats"]["pings_12h"] == 1
    # first_seen is the window's first fix, not the recording's oldest line.
    assert body["stats"]["first_seen"] == "2026-11-21T19:00:00Z"
