"""Prove the pure history mappers shape GFW records for the Hormuz snapshot."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from darkfleet.core.history import hotspots, to_history_items
from darkfleet.ingest.gfw import EncounterEvent, LoiteringEvent, parse_encounter_entries

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gfw" / "encounters_response.json"


def _fixture_encounters() -> list[EncounterEvent]:
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"]
    return parse_encounter_entries(entries)


def _encounter(**overrides: object) -> EncounterEvent:
    values: dict[str, object] = {
        "id": "enc",
        "start": datetime(2026, 10, 4, 6, tzinfo=UTC),
        "end": datetime(2026, 10, 4, 8, tzinfo=UTC),
        "vessel_id": "v-main",
        "mmsi": "400000001",
        "vessel_name": "MAIN",
        "flag": "PAN",
        "vessel_type": "tanker",
        "encountered_vessel_id": "v-other",
        "encountered_mmsi": "400000002",
        "encountered_name": "OTHER",
        "encountered_flag": "TZA",
        "lat": 25.01,
        "lon": 56.01,
        "median_distance_km": 0.3,
        "median_speed_knots": 0.9,
        "encounter_type": "carrier-tanker",
        "potential_risk": False,
    }
    values.update(overrides)
    return EncounterEvent(**values)  # type: ignore[arg-type]


def _loitering(**overrides: object) -> LoiteringEvent:
    values: dict[str, object] = {
        "id": "loi",
        "start": datetime(2026, 10, 4, 6, tzinfo=UTC),
        "end": datetime(2026, 10, 4, 13, tzinfo=UTC),
        "vessel_id": "v-loi",
        "mmsi": "400000003",
        "vessel_name": "DWELLER",
        "flag": "IRN",
        "vessel_type": "tanker",
        "lat": 25.02,
        "lon": 56.02,
        "duration_hours": 7.0,
        "average_speed_knots": 0.8,
        "total_distance_km": 4.2,
        "distance_from_shore_km": 18.5,
    }
    values.update(overrides)
    return LoiteringEvent(**values)  # type: ignore[arg-type]


def test_encounter_items_match_the_snapshot_shape() -> None:
    encounters = _fixture_encounters()

    items = to_history_items(encounters)

    assert len(items) == len(encounters)
    first = items[0]
    assert set(first) == {
        "id",
        "start",
        "end",
        "lat",
        "lon",
        "vessel",
        "other",
        "median_distance_km",
        "median_speed_kn",
    }
    assert first["id"] == "a6c5c721935594001866ed56a66ed194.1"
    assert first["start"] == "2026-09-05T00:00:00Z"
    assert first["end"] == "2026-09-05T03:30:00Z"
    assert first["lat"] == pytest.approx(59.6015)
    assert first["lon"] == pytest.approx(22.7159)
    assert first["vessel"] == {"name": "BALTIK", "mmsi": "230035000", "flag": "FIN", "type": "fishing"}
    assert first["other"] == {
        "name": "MENHADEN",
        "mmsi": "230122280",
        "flag": "FIN",
        "type": "fishing",
    }
    assert first["median_distance_km"] == pytest.approx(0.24)
    assert first["median_speed_kn"] == pytest.approx(1.966)


def test_encounter_without_partner_omits_other() -> None:
    event = _encounter(
        encountered_vessel_id=None,
        encountered_mmsi=None,
        encountered_name=None,
        encountered_flag=None,
    )

    item = to_history_items([event])[0]

    assert "other" not in item
    assert item["vessel"]["name"] == "MAIN"


def test_loitering_items_carry_hours_and_shore_distance() -> None:
    event = _loitering()

    item = to_history_items([event])[0]

    assert set(item) == {
        "id",
        "start",
        "end",
        "lat",
        "lon",
        "hours",
        "vessel",
        "distance_from_shore_km",
    }
    assert item["hours"] == pytest.approx(7.0)
    assert item["distance_from_shore_km"] == pytest.approx(18.5)
    assert item["vessel"] == {"name": "DWELLER", "mmsi": "400000003", "flag": "IRN", "type": "tanker"}
    assert item["start"] == "2026-10-04T06:00:00Z"
    assert item["end"] == "2026-10-04T13:00:00Z"


def test_loitering_hours_fall_back_to_the_event_clock() -> None:
    start = datetime(2026, 10, 4, 0, tzinfo=UTC)
    event = _loitering(start=start, end=start + timedelta(hours=8, minutes=30), duration_hours=None)

    item = to_history_items([event])[0]

    assert item["hours"] == pytest.approx(8.5)


def test_mixed_events_keep_their_order_and_family() -> None:
    events = [_loitering(), _encounter(), _loitering(id="loi-2")]

    items = to_history_items(events)

    assert [item["id"] for item in items] == ["loi", "enc", "loi-2"]
    assert "other" in items[1]
    assert "distance_from_shore_km" in items[2]


def test_unknown_records_are_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported history event"):
        to_history_items([object()])


def test_hotspots_pool_both_kinds_on_one_grid() -> None:
    items = [
        _encounter(lat=25.01, lon=56.01),
        _loitering(lat=25.04, lon=56.04),
        _encounter(lat=25.15, lon=56.15),
    ]

    cells = hotspots(to_history_items(items))

    assert cells == [
        {"lat": 25.05, "lon": 56.05, "n": 2},
        {"lat": 25.15, "lon": 56.15, "n": 1},
    ]


def test_hotspots_accept_raw_events_too() -> None:
    cells = hotspots([_encounter(lat=25.01, lon=56.01), _loitering(lat=25.01, lon=56.01)])

    assert cells == [{"lat": 25.05, "lon": 56.05, "n": 2}]


def test_hotspots_ties_break_on_position() -> None:
    items = [
        {"lat": 26.01, "lon": 58.01},
        {"lat": 24.01, "lon": 57.01},
        {"lat": 24.01, "lon": 57.01},
        {"lat": 26.01, "lon": 58.01},
    ]

    cells = hotspots(items, top=10)

    assert cells == [
        {"lat": 24.05, "lon": 57.05, "n": 2},
        {"lat": 26.05, "lon": 58.05, "n": 2},
    ]


def test_hotspots_respect_cell_size_and_top() -> None:
    items = [{"lat": 25.1, "lon": 56.1}, {"lat": 25.6, "lon": 56.6}, {"lat": 25.1, "lon": 57.1}]

    coarse = hotspots(items, cell_deg=1.0, top=2)

    assert coarse == [
        {"lat": 25.5, "lon": 56.5, "n": 2},
        {"lat": 25.5, "lon": 57.5, "n": 1},
    ]
    assert len(hotspots(items, top=1)) == 1


def test_hotspots_skip_missing_positions_and_empty_input() -> None:
    assert hotspots([]) == []
    assert hotspots([{"lat": None, "lon": 56.0}, {"name": "no position"}]) == []


def test_hotspots_reject_a_non_positive_cell() -> None:
    with pytest.raises(ValueError, match="cell_deg"):
        hotspots([], cell_deg=0.0)
