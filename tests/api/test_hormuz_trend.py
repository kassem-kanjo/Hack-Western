"""Contract tests for /api/hormuz/trend (#197): two years, pooled to weeks."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api import hormuz
from darkfleet.api.app import app
from darkfleet.ingest.regions import overlap_area, region_geometry

HORMUZ_LONG: dict[str, Any] = {
    "region": "HORMUZ",
    "period": {"start": "2025-01-01", "end": "2026-11-18"},
    "daily": [
        # Same ISO week (Mon 2024-12-30): pooled 55 dark / 150 seen.
        {"day": "2025-01-01", "seen": 100, "broadcasting": 70, "dark": 30, "dark_share": 0.3},
        {"day": "2025-01-02", "seen": 50, "broadcasting": 25, "dark": 25, "dark_share": 0.5},
        # Partial pass: under MIN_SEEN, must not enter any week.
        {"day": "2025-01-03", "seen": 10, "broadcasting": 1, "dark": 9, "dark_share": 0.9},
        # Next week (Mon 2025-01-06): pooled 25 dark / 60 seen.
        {"day": "2025-01-06", "seen": 40, "broadcasting": 20, "dark": 20, "dark_share": 0.5},
        {"day": "2025-01-08", "seen": 20, "broadcasting": 15, "dark": 5, "dark_share": 0.25},
        {"day": "2025-01-10", "seen": 19, "broadcasting": 0, "dark": 19, "dark_share": 1.0},
    ],
}

CONTROL_LONG: dict[str, Any] = {
    "region": "GULF_OF_OMAN_CONTROL",
    "period": {"start": "2025-01-01", "end": "2026-11-18"},
    "daily": [
        {"day": "2025-01-01", "seen": 25, "broadcasting": 20, "dark": 5, "dark_share": 0.2},
        {"day": "2025-01-03", "seen": 5, "broadcasting": 0, "dark": 5, "dark_share": 1.0},
        {"day": "2025-01-07", "seen": 30, "broadcasting": 18, "dark": 12, "dark_share": 0.4},
    ],
}

VALIDATION: dict[str, Any] = {
    "generated": "2026-11-21",
    "regime": {
        "baseline": {"start": "2025-01-01", "end": "2025-12-31"},
        "current": {"start": "2026-03-01", "end": "2026-06-30"},
        "treated": {
            "baseline": {"dark_share": 0.3164024587512132, "seen": 74184, "dark": 23472, "days": 203},
            "current": {"dark_share": 0.6596867624973182, "seen": 23305, "dark": 15374, "days": 66},
            "change": 0.34328430374610497,
        },
        "control": {
            "baseline": {"dark_share": 0.25965548504079783, "seen": 5515, "dark": 1432, "days": 112},
            "current": {"dark_share": 0.45785270629991126, "seen": 1127, "dark": 516, "days": 31},
            "change": 0.19819722125911343,
        },
        "did": 0.14508708248699154,
        "did_ci": [0.02203272418371862, 0.2584950899547915],
        "verdict": "moves with event",
    },
    "events": [
        {
            "date_start": "2026-04-08",
            "date_end": "2026-04-08",
            "title": "US-Iran two-week ceasefire declared",
            "type": "military",
            "source_url": "https://example.test/ceasefire",
            "confidence": "low",
            "window_days": 14,
            "treated": {
                "pre": {"dark_share": 0.6342427837089759, "seen": 2529, "dark": 1604, "days": 8},
                "post": {"dark_share": 0.4560810810810811, "seen": 3256, "dark": 1485, "days": 8},
                "change": -0.17816170262789482,
            },
            "control": {
                "pre": {"dark_share": 0.25, "seen": 140, "dark": 35, "days": 4},
                "post": {"dark_share": 0.417910447761194, "seen": 201, "dark": 84, "days": 5},
                "change": 0.16791044776119401,
            },
            "did": -0.34607215038908884,
            "did_ci": [-0.6688899495238667, -0.017345848331688],
            "verdict": "moves with event",
        },
        {
            "date_start": "2025-03-13",
            "date_end": "2025-03-13",
            "title": "OFAC sanctions Iran oil minister, shadow fleet operators",
            "type": "sanctions",
            "source_url": "https://example.test/ofac",
            "confidence": "high",
            "window_days": 14,
            "treated": {"pre": {"dark_share": 0.268}, "post": {"dark_share": 0.267}},
            "control": {"pre": {"dark_share": 0.236}, "post": {"dark_share": 0.219}},
            "did": 0.016,
            "did_ci": [-0.083, 0.139],
            "verdict": "no detectable change",
        },
    ],
}

FILES = {
    "hormuz_unseen_long.json": HORMUZ_LONG,
    "oman_control_long.json": CONTROL_LONG,
    "hormuz_validation.json": VALIDATION,
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA, so stale parses must not leak."""
    hormuz.reset_cache()
    yield
    hormuz.reset_cache()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Write the three trend snapshots into a temp directory and serve them."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    for name, payload in FILES.items():
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    with TestClient(app) as test_client:
        yield test_client


def test_trend_pools_days_into_iso_weeks(client: TestClient) -> None:
    response = client.get("/api/hormuz/trend")
    assert response.status_code == 200

    treated = response.json()["treated"]
    assert [week["week"] for week in treated] == ["2024-12-30", "2025-01-06"]
    assert treated[0]["seen"] == 150
    assert treated[0]["dark"] == 55
    assert treated[0]["dark_share"] == pytest.approx(55 / 150)
    assert treated[1]["seen"] == 60
    assert treated[1]["dark"] == 25
    assert treated[1]["dark_share"] == pytest.approx(25 / 60)


def test_trend_control_pools_its_own_days(client: TestClient) -> None:
    control = client.get("/api/hormuz/trend").json()["control"]
    assert [week["week"] for week in control] == ["2024-12-30", "2025-01-06"]
    assert control[0]["seen"] == 25
    assert control[0]["dark"] == 5
    assert control[1]["seen"] == 30
    assert control[1]["dark"] == 12
    assert control[1]["dark_share"] == pytest.approx(0.4)


def test_trend_events_carry_the_fields_the_panel_draws(client: TestClient) -> None:
    events = client.get("/api/hormuz/trend").json()["events"]
    assert len(events) == 2

    ceasefire = events[0]
    assert ceasefire["date_start"] == "2026-04-08"
    assert ceasefire["title"] == "US-Iran two-week ceasefire declared"
    assert ceasefire["type"] == "military"
    assert ceasefire["verdict"] == "moves with event"
    assert ceasefire["source_url"] == "https://example.test/ceasefire"
    assert ceasefire["treated"] == {
        "pre": pytest.approx(0.6342427837089759),
        "post": pytest.approx(0.4560810810810811),
    }
    assert ceasefire["control"] == {
        "pre": pytest.approx(0.25),
        "post": pytest.approx(0.417910447761194),
    }
    assert "confidence" not in ceasefire
    assert "window_days" not in ceasefire


def test_trend_regime_is_served_verbatim(client: TestClient) -> None:
    assert client.get("/api/hormuz/trend").json()["regime"] == VALIDATION["regime"]


def test_trend_areas_come_from_the_region_registry(client: TestClient) -> None:
    areas = client.get("/api/hormuz/trend").json()["areas"]
    treated, control = areas["treated"], areas["control"]

    assert treated["id"] == "HORMUZ"
    assert treated["label"] == "Hormuz (measured)"
    assert treated["geometry"] == region_geometry("HORMUZ")
    assert control["id"] == "GULF_OF_OMAN_CONTROL"
    assert control["label"] == "Gulf of Oman (comparison)"
    assert control["geometry"] == region_geometry("GULF_OF_OMAN_CONTROL")
    assert treated["geometry"]["type"] == "Polygon"
    assert control["geometry"]["type"] == "Polygon"
    assert overlap_area(treated["geometry"], control["geometry"]) == 0.0


def test_trend_unreadable_days_and_rows_are_skipped(client: TestClient, tmp_path: Path) -> None:
    noisy = dict(HORMUZ_LONG)
    noisy["daily"] = [
        {"day": "not a date", "seen": 100, "dark": 90},
        {"day": "2025-01-06", "seen": True, "dark": 5},
        {"day": "2025-01-06", "seen": 50, "dark": None},
        {"day": "2025-01-06", "seen": 50, "dark": -1},
        {"day": "2025-01-06", "seen": 50, "dark": 10, "dark_share": 0.2},
    ]
    path = tmp_path / "snapshot" / "hormuz_unseen_long.json"
    path.write_text(json.dumps(noisy), encoding="utf-8")
    hormuz.reset_cache()

    treated = client.get("/api/hormuz/trend").json()["treated"]
    assert treated == [{"week": "2025-01-06", "seen": 50, "dark": 10, "dark_share": pytest.approx(0.2)}]


def test_trend_weeks_are_sorted_even_when_the_file_is_not(client: TestClient, tmp_path: Path) -> None:
    shuffled = dict(HORMUZ_LONG)
    shuffled["daily"] = list(reversed(HORMUZ_LONG["daily"]))
    path = tmp_path / "snapshot" / "hormuz_unseen_long.json"
    path.write_text(json.dumps(shuffled), encoding="utf-8")
    hormuz.reset_cache()

    weeks = [week["week"] for week in client.get("/api/hormuz/trend").json()["treated"]]
    assert weeks == sorted(weeks)


@pytest.mark.parametrize(
    "name",
    ["hormuz_unseen_long.json", "oman_control_long.json", "hormuz_validation.json"],
)
def test_missing_file_is_404_json(client: TestClient, tmp_path: Path, name: str) -> None:
    (tmp_path / "snapshot" / name).unlink()
    hormuz.reset_cache()

    response = client.get("/api/hormuz/trend")
    assert response.status_code == 404
    assert response.json() == {"detail": f"snapshot missing: {name}"}
