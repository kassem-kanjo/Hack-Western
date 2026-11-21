"""Prove scripts/build_live.py turns one recorded hour into a complete live snapshot.

The fixture is a real aisstream sample, gzipped into the recorder's hour-file layout, so the test
exercises the same parse -> detect -> serialise path the CLI uses, not a hand-built shortcut.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from darkfleet.core.tracks import Event

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ais" / "baltic_sample.jsonl"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_live.py"

REGION = "SAMPLE"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_live", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_live = _load_script()


def _write_recording(tmp_path: Path) -> Path:
    """Lay the fixture out as one hourly recording under the region folder."""
    region_dir = tmp_path / "data" / "cache" / "ais" / REGION
    region_dir.mkdir(parents=True)
    text = FIXTURE.read_text(encoding="utf-8")
    with gzip.open(region_dir / "20261121T04.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(text)
    return region_dir


def _snapshot(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "out" / f"live_{REGION}.json").read_text(encoding="utf-8"))


def test_main_writes_every_contract_key(tmp_path: Path, monkeypatch) -> None:
    _write_recording(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert build_live.main(["--region", REGION, "--out", "out"]) == 0

    payload = _snapshot(tmp_path)
    assert set(payload) == {
        "region",
        "generated_at",
        "window",
        "coverage",
        "counts",
        "events",
        "watchlist",
        "vessels",
    }
    assert payload["region"] == REGION
    assert payload["window"]["start"].endswith("Z")
    assert payload["window"]["end"] >= payload["window"]["start"]
    assert set(payload["coverage"]) == {"minutes", "median_msgs_per_min", "holes"}
    assert payload["coverage"]["minutes"] >= 1
    assert set(payload["counts"]) == {
        "pings",
        "vessels",
        "silences",
        "jumps",
        "clones",
        "meetings",
        "spoofs",
        "watchlist",
        "watchlist_alerts",
        "watchlist_info",
    }
    assert payload["counts"]["pings"] > 0
    assert payload["counts"]["vessels"] > 0
    assert set(payload["watchlist"]) == {"alerts", "info"}
    assert payload["vessels"]
    for vessel in payload["vessels"]:
        assert set(vessel) == {"mmsi", "name", "imo", "ship_type", "class", "last"}
        assert set(vessel["last"]) == {"t", "lat", "lon", "sog"}
        assert vessel["class"] in {"tanker", "cargo", "fishing", "passenger", "default"}


def test_counts_match_the_events_written(tmp_path: Path, monkeypatch) -> None:
    _write_recording(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert build_live.main(["--region", REGION, "--out", "out", "--hours", "1"]) == 0

    payload = _snapshot(tmp_path)
    by_kind = Counter(event["kind"] for event in payload["events"])
    counts = payload["counts"]
    assert counts["silences"] == by_kind["silence"]
    assert counts["jumps"] == by_kind["jump"]
    assert counts["clones"] == by_kind["clone"]
    assert counts["meetings"] == by_kind["meeting"]
    assert counts["spoofs"] == by_kind["spoof"]
    assert counts["vessels"] == len(payload["vessels"])
    assert counts["watchlist"] == len(payload["watchlist"]["alerts"]) + len(payload["watchlist"]["info"])
    assert counts["watchlist_alerts"] == len(payload["watchlist"]["alerts"])
    assert counts["watchlist_info"] == len(payload["watchlist"]["info"])
    assert len(payload["events"]) <= 500

    severities = [event["severity"] for event in payload["events"]]
    assert severities == sorted(severities, reverse=True)
    for event in payload["events"]:
        assert event["start"].endswith("Z")
        assert event["end"].endswith("Z")


def test_default_meetings_use_cargo_tanker_types_and_five_km_offshore(tmp_path: Path, monkeypatch) -> None:
    region_dir = _write_recording(tmp_path)
    captured: dict = {}

    def fake_meetings(tracks, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(build_live, "meetings", fake_meetings)

    build_live.build(REGION, region_dir, tmp_path / "out")

    assert captured["min_offshore_km"] == 5.0
    assert captured["ship_types"] == frozenset(range(70, 90))
    # 266128000 is a fishing hull (type 30) in the fixture: it carries a known type, so
    # the filter can positively exclude it rather than relying on "unknown" handling.
    assert captured["vessel_types"][266128000] == 30
    assert 266128000 not in captured["ship_types"]


def test_cli_all_meeting_types_remove_the_type_filter(tmp_path: Path, monkeypatch) -> None:
    _write_recording(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict = {}

    def fake_meetings(tracks, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(build_live, "meetings", fake_meetings)

    code = build_live.main(
        ["--region", REGION, "--out", "out", "--meeting-types", "all", "--min-offshore-km", "12.5"]
    )

    assert code == 0
    assert captured["min_offshore_km"] == 12.5
    assert captured.get("ship_types") is None
    assert "vessel_types" not in captured


def test_cli_cargo_tanker_is_the_default_meeting_filter(tmp_path: Path, monkeypatch) -> None:
    _write_recording(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict = {}

    def fake_meetings(tracks, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(build_live, "meetings", fake_meetings)

    assert build_live.main(["--region", REGION, "--out", "out"]) == 0

    assert captured["ship_types"] == frozenset(range(70, 90))
    assert captured["min_offshore_km"] == 5.0


def test_watchlist_is_split_into_alerts_and_info(tmp_path: Path, monkeypatch) -> None:
    _write_recording(tmp_path)
    monkeypatch.chdir(tmp_path)
    rows = [
        {"mmsi": 1, "category": "sanctioned"},
        {"mmsi": 2, "category": "shadow_fleet"},
        {"mmsi": 3, "category": "detained"},
        {"mmsi": 4, "category": "other"},
    ]
    monkeypatch.setattr(build_live, "_watchlist_rows", lambda *args, **kwargs: rows)

    assert build_live.main(["--region", REGION, "--out", "out"]) == 0

    watchlist = _snapshot(tmp_path)["watchlist"]
    assert [case["mmsi"] for case in watchlist["alerts"]] == [1, 2]
    assert [case["mmsi"] for case in watchlist["info"]] == [3, 4]
    counts = _snapshot(tmp_path)["counts"]
    assert counts["watchlist"] == 4
    assert counts["watchlist_alerts"] == 2
    assert counts["watchlist_info"] == 2


def test_coverage_holes_merge_adjacent_quiet_minutes() -> None:
    rows = [
        build_live.CoverageMinute(datetime(2026, 11, 21, 4, m, tzinfo=UTC), n)
        for m, n in ((0, 150), (1, 10), (2, 10), (3, 150), (4, 5), (5, 150))
    ]

    summary = build_live._coverage_summary(rows)

    assert summary["minutes"] == 6
    assert summary["median_msgs_per_min"] == 80.0
    assert summary["holes"] == [
        {"start": "2026-11-21T04:01:00Z", "end": "2026-11-21T04:02:00Z"},
        {"start": "2026-11-21T04:04:00Z", "end": "2026-11-21T04:04:00Z"},
    ]


def _meeting(lat: float, lon: float, severity: float) -> Event:
    """A meeting event with the shape ``core.encounters`` produces, for context tests."""
    return Event(
        kind="meeting",
        mmsi=111111111,
        other_mmsi=222222222,
        start=datetime(2026, 11, 21, 4, 0, tzinfo=UTC),
        end=datetime(2026, 11, 21, 4, 45, tzinfo=UTC),
        lat=lat,
        lon=lon,
        severity=severity,
        explanation="Two vessels stayed within 200 m of each other for 45 min.",
        evidence={"minutes": 45.0},
    )


def _only_meeting(payload: dict) -> dict:
    events = [event for event in payload["events"] if event["kind"] == "meeting"]
    assert len(events) == 1
    return events[0]


def test_meeting_inside_singapore_anchorage_gets_context_and_half_severity(
    tmp_path: Path, monkeypatch
) -> None:
    region_dir = _write_recording(tmp_path)
    monkeypatch.setattr(build_live, "meetings", lambda *args, **kwargs: [_meeting(1.28, 103.95, 0.8)])

    payload = build_live.build(REGION, region_dir, tmp_path / "out")

    event = _only_meeting(payload)
    assert event["severity"] == 0.4
    assert event["evidence"]["context"] == {
        "name": "Singapore Eastern anchorage",
        "kind": "bunkering",
        "note": "approximate box drawn by hand, not an official boundary",
    }
    assert "In a known anchorage (Singapore Eastern anchorage)" in event["explanation"]
    assert "bunkering" in event["explanation"]


def test_meeting_in_open_water_keeps_its_severity_and_no_context(tmp_path: Path, monkeypatch) -> None:
    region_dir = _write_recording(tmp_path)
    monkeypatch.setattr(build_live, "meetings", lambda *args, **kwargs: [_meeting(58.0, 19.0, 0.8)])

    payload = build_live.build(REGION, region_dir, tmp_path / "out")

    event = _only_meeting(payload)
    assert event["severity"] == 0.8
    assert "context" not in event["evidence"]
    assert "known anchorage" not in event["explanation"]


def test_meeting_with_a_watchlist_alert_is_floored_at_point_nine(tmp_path: Path, monkeypatch) -> None:
    region_dir = _write_recording(tmp_path)
    monkeypatch.setattr(build_live, "meetings", lambda *args, **kwargs: [_meeting(58.0, 19.0, 0.5)])
    monkeypatch.setattr(
        build_live,
        "_watchlist_rows",
        lambda *args, **kwargs: [{"mmsi": 222222222, "category": "sanctioned"}],
    )

    payload = build_live.build(REGION, region_dir, tmp_path / "out")

    event = _only_meeting(payload)
    assert event["severity"] == 0.9
    assert event["evidence"]["watchlist"] == ["sanctioned"]
    assert event["explanation"].endswith(" One vessel is on a sanctions list.")
