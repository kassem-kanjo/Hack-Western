"""Offline test of scripts/build_hormuz_history.py with a fake GFW client."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from darkfleet.ingest.gfw import EncounterEvent, LoiteringEvent
from darkfleet.ingest.regions import region_geometry

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_hormuz_history.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_hormuz_history", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_hormuz_history = _load_script()

START = date(2026, 8, 15)
END = date(2026, 11, 15)
T0 = datetime(2026, 10, 4, 6, tzinfo=UTC)


def _encounter(i: int, lat: float, lon: float) -> EncounterEvent:
    return EncounterEvent(
        id=f"enc-{i}",
        start=T0,
        end=T0 + timedelta(hours=2),
        vessel_id=f"v-{i}",
        mmsi=f"40000000{i}",
        vessel_name=f"MAIN {i}",
        flag="PAN",
        vessel_type="tanker",
        encountered_vessel_id=f"other-{i}",
        encountered_mmsi=f"41000000{i}",
        encountered_name=f"OTHER {i}",
        encountered_flag="TZA",
        lat=lat,
        lon=lon,
        median_distance_km=0.25,
        median_speed_knots=1.1,
        encounter_type="carrier-tanker",
        potential_risk=False,
        encountered_vessel_type="fishing",
    )


def _loitering(i: int, lat: float, lon: float, hours: float | None) -> LoiteringEvent:
    return LoiteringEvent(
        id=f"loi-{i}",
        start=T0,
        end=T0 + timedelta(hours=hours if hours is not None else 1.0),
        vessel_id=f"lv-{i}",
        mmsi=f"42000000{i}",
        vessel_name=f"DWELL {i}",
        flag="IRN",
        vessel_type="tanker",
        lat=lat,
        lon=lon,
        duration_hours=hours,
        average_speed_knots=0.7,
        total_distance_km=3.0,
        distance_from_shore_km=12.0,
    )


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], date, date]] = []
        self.encounter_events = [_encounter(1, 25.01, 56.01), _encounter(2, 25.02, 56.02)]
        self.loitering_events = [
            _loitering(1, 25.01, 56.01, 6.5),  # kept: just over the floor
            _loitering(2, 26.5, 58.5, 6.0),  # dropped: the floor is strict
            _loitering(3, 24.2, 57.2, None),  # dropped: its clock spans only an hour
        ]

    def encounters(self, region: dict, start: date, end: date) -> list[EncounterEvent]:
        self.calls.append(("encounters", region, start, end))
        return self.encounter_events

    def loitering(self, region: dict, start: date, end: date) -> list[LoiteringEvent]:
        self.calls.append(("loitering", region, start, end))
        return self.loitering_events


def test_main_writes_the_history_snapshot(tmp_path: Path, capsys) -> None:
    client = FakeClient()
    out = tmp_path / "hormuz_history.json"

    code = build_hormuz_history.main(
        ["--start", "2026-08-15", "--end", "2026-11-15", "--out", str(out)], client=client
    )

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["region"] == "HORMUZ"
    assert payload["period"] == {"start": "2026-08-15", "end": "2026-11-15"}
    assert payload["counts"] == {"encounters": 2, "loitering": 1, "total": 3}

    assert [item["id"] for item in payload["encounters"]] == ["enc-1", "enc-2"]
    first = payload["encounters"][0]
    assert first["vessel"] == {"name": "MAIN 1", "mmsi": "400000001", "flag": "PAN", "type": "tanker"}
    assert first["other"] == {
        "name": "OTHER 1",
        "mmsi": "410000001",
        "flag": "TZA",
        "type": "fishing",
    }
    assert first["median_distance_km"] == 0.25
    assert first["median_speed_kn"] == 1.1
    assert first["start"] == "2026-10-04T06:00:00Z"

    assert [item["id"] for item in payload["loitering"]] == ["loi-1"]
    dwell = payload["loitering"][0]
    assert dwell["hours"] == 6.5
    assert dwell["distance_from_shore_km"] == 12.0
    assert dwell["lat"] == 25.01

    # Both kinds are pooled; the dropped loitering events do not contribute cells.
    assert payload["hotspots"] == [{"lat": 25.05, "lon": 56.05, "n": 3}]
    assert "Global Fishing Watch" in payload["source"]
    assert payload["caveats"] == [
        "GFW events are AIS-based: ships with AIS off are not in this layer",
        "Encounters cover fishing/carrier/tanker-fishing types per GFW",
        "Non-commercial data (CC BY-NC)",
    ]

    assert [call[0] for call in client.calls] == ["encounters", "loitering"]
    for _, region, start, end in client.calls:
        assert region == region_geometry("HORMUZ")
        assert (start, end) == (START, END)

    printed = capsys.readouterr().out
    assert "encounters=2 loitering=1" in printed


def test_inverted_range_is_rejected_before_any_fetch(tmp_path: Path) -> None:
    client = FakeClient()

    code = build_hormuz_history.main(
        [
            "--start",
            "2026-11-15",
            "--end",
            "2026-08-15",
            "--out",
            str(tmp_path / "x.json"),
        ],
        client=client,
    )

    assert code == 2
    assert client.calls == []
    assert not (tmp_path / "x.json").exists()


def test_missing_token_fails_without_network(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GFW_API_TOKEN", raising=False)
    monkeypatch.setattr(build_hormuz_history, "load_dotenv", lambda *args, **kwargs: None)

    code = build_hormuz_history.main(
        ["--start", "2026-08-15", "--end", "2026-11-15", "--out", str(tmp_path / "x.json")]
    )

    assert code == 2
    assert not (tmp_path / "x.json").exists()
