"""Contract tests for the Hormuz snapshot router (#88/#89)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import hormuz
from darkfleet.api.app import app

UNSEEN: dict[str, Any] = {
    "region": "HORMUZ",
    "period": {"start": "2026-08-15", "end": "2026-11-15"},
    "daily": [
        {"day": "2026-08-15", "seen": 100, "broadcasting": 60, "dark": 40, "dark_share": 0.4},
        {"day": "2026-08-16", "seen": 80, "broadcasting": 32, "dark": 48, "dark_share": 0.6},
    ],
    "grid": [{"lat": 26.15, "lon": 56.35, "n": 12}, {"lat": 26.25, "lon": 56.45, "n": 3}],
    "summary": {
        "days": 2,
        "seen_total": 180,
        "dark_total": 88,
        "dark_share_mean": 0.5,
        "dark_share_ci": [0.1, 0.9],
        "busiest_day": "2026-08-15",
    },
    "source": "Global Fishing Watch SAR vessel detections (Sentinel-1)",
    "caveats": ["Radar misses vessels under ~25 m", "'Dark' means no AIS match, not wrongdoing"],
}

HISTORY: dict[str, Any] = {
    "region": "HORMUZ",
    "period": {"start": "2026-08-15", "end": "2026-11-15"},
    "encounters": [],
    "loitering": [],
    "counts": {"encounters": 0, "loitering": 0, "total": 0},
    "hotspots": [{"lat": 24.85, "lon": 56.85, "n": 155}, {"lat": 25.55, "lon": 54.95, "n": 154}],
    "source": "Powered by Global Fishing Watch, v3 events API",
    "caveats": ["GFW events are AIS-based: ships with AIS off are not in this layer"],
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA, so stale parses must not leak."""
    hormuz.reset_cache()
    yield
    hormuz.reset_cache()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Write both Hormuz snapshots into a temp directory and serve them."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    (directory / "hormuz_unseen.json").write_text(json.dumps(UNSEEN), encoding="utf-8")
    (directory / "hormuz_history.json").write_text(json.dumps(HISTORY), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    with TestClient(app) as test_client:
        yield test_client


def test_unseen_returns_the_file_verbatim(client: TestClient) -> None:
    response = client.get("/api/hormuz/unseen")
    assert response.status_code == 200
    assert response.json() == UNSEEN


def test_history_returns_the_file_verbatim(client: TestClient) -> None:
    response = client.get("/api/hormuz/history")
    assert response.status_code == 200
    assert response.json() == HISTORY


def test_missing_unseen_is_404_json(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "snapshot" / "hormuz_unseen.json").unlink()
    hormuz.reset_cache()

    response = client.get("/api/hormuz/unseen")
    assert response.status_code == 404
    assert response.json() == {"detail": "snapshot missing: hormuz_unseen.json"}


def test_missing_history_is_404_json(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "snapshot" / "hormuz_history.json").unlink()
    hormuz.reset_cache()

    response = client.get("/api/hormuz/history")
    assert response.status_code == 404
    assert response.json() == {"detail": "snapshot missing: hormuz_history.json"}


def test_rebuilt_file_is_read_again(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "snapshot" / "hormuz_unseen.json"
    rebuilt = dict(UNSEEN, region="REBUILT")
    path.write_text(json.dumps(rebuilt), encoding="utf-8")

    assert client.get("/api/hormuz/unseen").json()["region"] == "REBUILT"
