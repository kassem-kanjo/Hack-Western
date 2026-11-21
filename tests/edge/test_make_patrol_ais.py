"""scripts/make_patrol_ais.py: filter watch.json ships to the patrol radius for the edge."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_patrol_ais.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_patrol_ais", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def patrol() -> ModuleType:
    return _load()


def test_select_ships_filters_by_radius_and_converts_iso_time(patrol: ModuleType) -> None:
    watch = {
        "ships": [
            {"mmsi": 111, "name": "NEAR", "lat": 26.58, "lon": 56.25, "t": "2026-11-21T12:00:00Z"},
            {"mmsi": 222, "name": "FAR", "lat": 27.5, "lon": 56.25, "t": "2026-11-21T12:00:00Z"},
            {"mmsi": 333, "name": None, "lat": 26.5705, "lon": 56.2505, "t": 1_700_000_000},
        ]
    }

    ships = patrol.select_ships(watch, 26.57, 56.25, 2.0)

    assert [ship["mmsi"] for ship in ships] == [333, 111]  # nearest first
    assert ships[0]["name"] == ""
    assert ships[0]["t"] == 1_700_000_000.0
    assert ships[1]["t"] > 1_700_000_000.0


def test_select_ships_skips_unusable_records(patrol: ModuleType) -> None:
    watch = {
        "ships": [
            {"mmsi": "bad", "lat": 26.57, "lon": 56.25, "t": 1},
            {"mmsi": 1, "lat": 26.57, "lon": 56.25},
            {"mmsi": 2, "lat": 999.0, "lon": 56.25, "t": 1},
            "not a dict",
        ]
    }
    assert patrol.select_ships(watch, 26.57, 56.25, 2.0) == []


def test_select_ships_rejects_a_file_without_a_ships_list(patrol: ModuleType) -> None:
    with pytest.raises(ValueError, match="ships"):
        patrol.select_ships({"ships": None}, 26.57, 56.25, 2.0)
