"""Offline tests for the pure GFW parsers."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from darkfleet.ingest.gfw import (
    EncounterEvent,
    GapEvent,
    LoiteringEvent,
    SarCellRow,
    parse_encounter_entries,
    parse_gap_entries,
    parse_loitering_entries,
    parse_sar_report,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gfw"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_gap_entries_fixture():
    entries = _load("gaps_entries.json")

    gaps = parse_gap_entries(entries)

    assert isinstance(gaps[0], GapEvent)
    assert len(gaps) == 3
    first = gaps[0]
    assert first.id == "2366b894a2107aff7c4bf6699fdd58f9"
    assert first.vessel_id == "902ec1b33-3111-d4b8-a254-788b8621a875"
    assert first.mmsi == "416004522"
    assert first.vessel_name == "FENG YA NO18"
    assert first.flag == "TWN"
    assert first.vessel_type == "fishing"
    assert first.intentional_disabling is True
    assert first.implied_speed_knots == pytest.approx(10.644790701130152)
    assert first.distance_km == pytest.approx(14440.364714187981)
    assert first.duration_hours == pytest.approx(732.4872222222223)
    assert first.off_lat == pytest.approx(5.041322)
    assert first.off_lon == pytest.approx(-16.693795)
    assert first.on_lat == pytest.approx(22.573778)
    assert first.on_lon == pytest.approx(120.324688)
    assert first.start == datetime(2026, 7, 19, 11, 58, tzinfo=UTC)
    assert first.end == datetime(2026, 8, 19, 0, 27, 14, tzinfo=UTC)
    assert first.start.tzinfo is not None
    assert first.start.utcoffset() == timedelta(0)


def test_parse_gap_entries_missing_required_field_names_entry():
    entries = _load("gaps_entries.json")
    broken = json.loads(json.dumps(entries[0]))
    del broken["start"]

    with pytest.raises(ValueError, match=broken["id"]):
        parse_gap_entries([broken])


def test_parse_gap_entries_missing_vessel_id_names_entry():
    entries = _load("gaps_entries.json")
    broken = json.loads(json.dumps(entries[0]))
    del broken["vessel"]["id"]

    with pytest.raises(ValueError, match=broken["id"]):
        parse_gap_entries([broken])


def test_parse_encounter_entries_fixture():
    entries = _load("encounters_response.json")["entries"]

    encounters = parse_encounter_entries(entries)

    assert isinstance(encounters[0], EncounterEvent)
    assert len(encounters) == len(entries)
    first = encounters[0]
    assert first.id == "a6c5c721935594001866ed56a66ed194.1"
    assert first.vessel_id == "4a51c1515-5955-fe28-4c87-64d89f906be6"
    assert first.mmsi == "230035000"
    assert first.vessel_name == "BALTIK"
    assert first.flag == "FIN"
    assert first.vessel_type == "fishing"
    assert first.encountered_vessel_id == "bc1f4b9e9-94cf-bee3-b8ba-6d2e466eb9b3"
    assert first.encountered_mmsi == "230122280"
    assert first.encountered_name == "MENHADEN"
    assert first.encountered_flag == "FIN"
    assert first.lat == pytest.approx(59.6015)
    assert first.lon == pytest.approx(22.7159)
    assert first.median_distance_km == pytest.approx(0.24)
    assert first.median_speed_knots == pytest.approx(1.966)
    assert first.encounter_type == "fishing-fishing"
    assert first.potential_risk is False
    assert first.start == datetime(2026, 9, 5, 0, 0, tzinfo=UTC)


def test_parse_loitering_inline_dict():
    entries = [
        {
            "id": "loiter-1",
            "start": "2026-10-01T00:00:00.000Z",
            "end": "2026-10-01T05:30:00.000Z",
            "vessel": {
                "id": "v-1",
                "ssvid": "123456789",
                "name": "SHADOW",
                "flag": "PAN",
                "type": "tanker",
            },
            "position": {"lat": 25.5, "lon": 56.5},
            "loitering": {
                "averageSpeedKnots": 1.2,
                "totalDistanceKm": 12.5,
                "timeInHours": 5.5,
            },
        }
    ]

    loitering = parse_loitering_entries(entries)

    assert isinstance(loitering[0], LoiteringEvent)
    first = loitering[0]
    assert first.id == "loiter-1"
    assert first.vessel_id == "v-1"
    assert first.mmsi == "123456789"
    assert first.vessel_name == "SHADOW"
    assert first.flag == "PAN"
    assert first.vessel_type == "tanker"
    assert first.lat == pytest.approx(25.5)
    assert first.lon == pytest.approx(56.5)
    assert first.duration_hours == pytest.approx(5.5)
    assert first.average_speed_knots == pytest.approx(1.2)
    assert first.total_distance_km == pytest.approx(12.5)
    assert first.start == datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    assert first.end == datetime(2026, 10, 1, 5, 30, tzinfo=UTC)


def test_parse_loitering_missing_optional_keys():
    entries = [
        {
            "id": "loiter-2",
            "start": "2026-10-02T00:00:00.000Z",
            "end": "2026-10-02T01:00:00.000Z",
            "vessel": {"id": "v-2"},
            "position": {"lat": 1.0, "lon": 2.0},
        }
    ]

    first = parse_loitering_entries(entries)[0]

    assert first.mmsi is None
    assert first.vessel_name is None
    assert first.flag is None
    assert first.vessel_type is None
    assert first.duration_hours is None
    assert first.average_speed_knots is None
    assert first.total_distance_km is None


def test_parse_sar_report_fixture():
    payload = _load("sar_4wings_report_unmatched.json")
    expected_count = sum(len(rows) for group in payload["entries"] for rows in group.values())

    rows = parse_sar_report(payload)

    assert len(rows) == expected_count
    assert all(isinstance(row, SarCellRow) for row in rows)
    assert all(isinstance(row.day, date) for row in rows)
    assert all(row.detections >= 1 for row in rows)
    assert rows[0].day == date(2026, 10, 7)
    assert rows[0].lat == pytest.approx(25.16)
    assert rows[0].lon == pytest.approx(56.58)
    assert rows[0].detections == 1
    assert rows[0].vessel_ids == 1
