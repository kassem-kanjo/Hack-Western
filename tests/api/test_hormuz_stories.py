"""Contract for GET /api/hormuz/stories (#219): the snapshot as written, 404 JSON when absent."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import hormuz
from darkfleet.api.app import app

STORIES: dict[str, Any] = {
    "generated_at": "2026-11-21T19:00:00Z",
    "source": "Powered by Global Fishing Watch (public-global-gaps-events:latest)",
    "listed": [{"mmsi": "314001149", "name": "SEA 1", "hours": 13.1, "moved_km": 96.1}],
    "clusters": [{"day": "2026-03-12", "vessels": 10, "span_hours": 1.73}],
    "counts": {"gaps_in_box": 31, "listed": 1, "clusters": 1, "cluster_vessels": 10},
    "caveats": ["AIS gaps show a transponder went silent; they do not show why"],
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DARKFLEET_DATA", str(tmp_path))
    hormuz.reset_cache()
    with TestClient(app) as test_client:
        yield test_client
    hormuz.reset_cache()


def test_missing_snapshot_is_a_404_json(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/hormuz/stories")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "hormuz_stories.json" in response.json()["detail"]


def test_snapshot_is_served_as_written(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "hormuz_stories.json").write_text(json.dumps(STORIES), encoding="utf-8")
    response = client.get("/api/hormuz/stories")
    assert response.status_code == 200
    assert response.json() == STORIES
