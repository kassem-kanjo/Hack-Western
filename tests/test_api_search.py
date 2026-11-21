"""Contract tests for the ship search router (#183)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import search
from darkfleet.api.app import app, reset_cache

AREAS: list[dict[str, Any]] = [
    {"id": "HORMUZ_STRAIT", "name": "Strait of Hormuz", "kind": "watched", "bbox": [55, 25, 57.5, 27.5]},
    {"id": "HORMUZ_WATCH", "name": "Gulf of Hormuz", "kind": "listening", "bbox": [54, 23.5, 59, 28]},
    {"id": "BALTIC_WATCH", "name": "Baltic", "kind": "listening", "bbox": [9, 53.5, 31, 61]},
]

SHIPS: list[dict[str, Any]] = [
    {
        "mmsi": 422000111,
        "name": "GESTIA",
        "imo": "9123456",
        "lat": 26.6,
        "lon": 56.33,
        "class": "tanker",
        "watchlist": "sanctioned",
    },
    {
        "mmsi": 422000112,
        "name": "GESSA",
        "lat": 26.2,
        "lon": 56.1,
        "class": "tanker",
        "watchlist": None,
    },
    {
        "mmsi": 273128130,
        "name": "AFRA STAR",
        "lat": 59.9,
        "lon": 28.9,
        "class": "cargo",
        "watchlist": None,
    },
    {
        "mmsi": 273555555,
        "name": "STARLIGHT",
        "lat": 60.1,
        "lon": 29.2,
        "class": "bulk",
        "watchlist": "shadow_fleet",
    },
    {
        "mmsi": 311000001,
        "name": "NORTH WIND",
        "lat": 10.0,
        "lon": 5.0,
        "class": "tanker",
        "watchlist": None,
    },
]

# Enough hulls to prove the cap; none of their names collide with the queries above.
SHIPS.extend(
    {
        "mmsi": 311100000 + index,
        "name": f"TEST SHIP {index:02d}",
        "lat": 1.0,
        "lon": 1.0,
        "class": "cargo",
        "watchlist": None,
    }
    for index in range(1, 26)
)

WATCH: dict[str, Any] = {
    "generated_at": "2026-11-21T19:20:00Z",
    "refresh_s": 300,
    "areas": AREAS,
    "ships": SHIPS,
    "alerts": [],
    "counts": {"ships": len(SHIPS), "alerts": 0, "hidden_routine": 0},
}


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA, so stale parses must not leak."""
    reset_cache()
    search.reset_cache()
    yield
    reset_cache()
    search.reset_cache()


@pytest.fixture
def watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A snapshot directory holding the test watch file."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    (directory / "watch.json").write_text(json.dumps(WATCH), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    return directory


def _search(client: TestClient, query: str | None = None) -> list[dict[str, Any]]:
    params = {} if query is None else {"q": query}
    response = client.get("/api/search", params=params)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    return body


def test_short_or_empty_query_is_empty(watch_dir: Path) -> None:
    with TestClient(app) as client:
        assert _search(client) == []
        assert _search(client, "") == []
        assert _search(client, "a") == []
        assert _search(client, "  g ") == []


def test_name_substring_is_case_insensitive(watch_dir: Path) -> None:
    with TestClient(app) as client:
        rows = _search(client, "StAr")

    names = [row["name"] for row in rows]
    assert names[:2] == ["STARLIGHT", "AFRA STAR"]
    assert "TEST SHIP 01" not in names


def test_name_prefix_ranks_before_substring(watch_dir: Path) -> None:
    with TestClient(app) as client:
        rows = _search(client, "star")

    assert rows[0]["name"] == "STARLIGHT"


def test_mmsi_exact_and_prefix(watch_dir: Path) -> None:
    with TestClient(app) as client:
        exact = _search(client, "273128130")
        prefix = _search(client, "273")

    assert [row["mmsi"] for row in exact] == [273128130]
    # Prefix matches keep watchlist order first: shadow fleet, then the rest by name.
    assert [row["mmsi"] for row in prefix] == [273555555, 273128130]


def test_imo_exact_and_prefix(watch_dir: Path) -> None:
    with TestClient(app) as client:
        exact = _search(client, "9123456")
        prefix = _search(client, "912")

    assert [row["mmsi"] for row in exact] == [422000111]
    assert [row["mmsi"] for row in prefix] == [422000111]


def test_sanctioned_hulls_sort_first(watch_dir: Path) -> None:
    with TestClient(app) as client:
        rows = _search(client, "ges")

    assert [row["name"] for row in rows] == ["GESTIA", "GESSA"]
    assert rows[0]["watchlist"] == "sanctioned"


def test_row_shape_and_area(watch_dir: Path) -> None:
    with TestClient(app) as client:
        rows = _search(client, "gestia")
        outside = _search(client, "north wind")

    assert rows == [
        {
            "mmsi": 422000111,
            "name": "GESTIA",
            "imo": "9123456",
            "type": "tanker",
            "watchlist": "sanctioned",
            "lat": 26.6,
            "lon": 56.33,
            "area": "HORMUZ_STRAIT",
        }
    ]
    assert outside[0]["area"] is None


def test_results_are_capped(watch_dir: Path) -> None:
    with TestClient(app) as client:
        rows = _search(client, "test ship")

    assert len(rows) == search.MAX_RESULTS


def test_no_match_is_empty(watch_dir: Path) -> None:
    with TestClient(app) as client:
        assert _search(client, "zzzz") == []


def test_missing_snapshot_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DARKFLEET_DATA", str(empty))

    with TestClient(app) as client:
        assert _search(client, "gestia") == []
