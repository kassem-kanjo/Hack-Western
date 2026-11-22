"""Prove scripts/freeze_case.py picks the demo alert and freezes its story case.

Everything here is synthetic: fake names ("TEST HULL A"), MMSIs in the 999000000 range and
fake IMO numbers. No real vessel, sanctions row or snapshot value is copied into a test.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from darkfleet.api import story, vessels, watch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "freeze_case.py"
GENERATED_AT = "2026-11-21T19:20:00Z"
NOW = datetime(2026, 11, 21, 19, 20, tzinfo=UTC)
T_START = "2026-11-21T10:00:00Z"


def _load_script():
    spec = importlib.util.spec_from_file_location("freeze_case", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


freeze_case = _load_script()


def _reset() -> None:
    vessels.reset_cache()
    watch.reset_cache()


@pytest.fixture(autouse=True)
def _isolated_caches() -> Iterator[None]:
    _reset()
    yield
    _reset()


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A snapshot directory plus an empty recorder dir, so dossiers come only from watch."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    ais = tmp_path / "ais"
    ais.mkdir()
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    monkeypatch.setenv("DARKFLEET_AIS_DIR", str(ais))
    _write_scenario(directory)
    return directory


def _alert(
    alert_id: str,
    mmsi: int,
    name: str,
    *,
    status: str = "ongoing",
    contacts: list[dict[str, Any]] | None = None,
    speed_cap_kn: float | None = 12.0,
    last: dict[str, Any] | None = None,
    trail: list[list[Any]] | None = None,
    minutes: int | None = 60,
    t_start: str = T_START,
    t_end: str | None = None,
) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "id": alert_id,
        "kind": "dark",
        "status": status,
        "mmsi": mmsi,
        "name": name,
        "label": "Went dark",
        "area_id": "TEST_AREA",
        "t_start": t_start,
        "t_end": t_end,
        "last": last
        if last is not None
        else {"lat": 10.0, "lon": 20.0, "t": "2026-11-21T12:00:00Z", "sog": 0.0, "cog": 0.0},
        "speed_cap_kn": speed_cap_kn,
        "trail": trail if trail is not None else [[20.0, 10.0, "2026-11-21T11:00:00Z"]],
        "contacts": list(contacts or []),
        "evidence": {"minutes": minutes} if minutes is not None else {},
    }
    return alert


def _contact(
    mmsi: int,
    name: str,
    *,
    watchlist: str | None = None,
    could_meet: bool = True,
    lat: float = 10.1,
    lon: float = 20.1,
) -> dict[str, Any]:
    return {
        "mmsi": mmsi,
        "name": name,
        "watchlist": watchlist,
        "lat": lat,
        "lon": lon,
        "t": "2026-11-21T11:30:00Z",
        "min_dist_km": 1.0,
        "stopped": True,
        "could_meet": could_meet,
    }


def _ship(
    mmsi: int,
    name: str,
    *,
    watchlist: str | None = None,
    valid: bool | None = True,
    lat: float = 10.0,
    lon: float = 20.0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "mmsi": mmsi,
        "name": name,
        "watchlist": watchlist,
        "lat": lat,
        "lon": lon,
        "t": "2026-11-21T11:00:00Z",
    }
    if valid is not None:
        row["valid"] = valid
    return row


def _write_watch(data: Path, alerts: list[dict[str, Any]], ships: list[dict[str, Any]]) -> None:
    payload = {"generated_at": GENERATED_AT, "ships": ships, "alerts": alerts}
    (data / "watch.json").write_text(json.dumps(payload), encoding="utf-8")


def _freeze(data: Path, *, now: datetime = NOW) -> dict[str, Any]:
    out = data / "story_case.json"
    return freeze_case.freeze(data, out, now)


# ---- selection rules --------------------------------------------------------


def test_rule_1_picks_a_watchlisted_hull_and_ongoing_wins(data: Path) -> None:
    ongoing = _alert(
        "dark-999000001-1000",
        999000001,
        "TEST HULL A",
        status="ongoing",
        contacts=[_contact(999000015, "TEST CONTACT 5")],
    )
    ended = _alert(
        "dark-999000002-1000",
        999000002,
        "TEST HULL B",
        status="ended",
        contacts=[
            _contact(999000011, "TEST CONTACT 1", watchlist="sanctioned"),
            _contact(999000012, "TEST CONTACT 2", watchlist="shadow_fleet"),
            _contact(999000013, "TEST CONTACT 3", watchlist="sanctioned"),
        ],
    )
    plain = _alert(
        "dark-999000003-1000",
        999000003,
        "TEST HULL C",
        contacts=[_contact(999000014, "TEST CONTACT 4", watchlist="sanctioned")],
    )
    ships = [
        _ship(999000001, "TEST HULL A", watchlist="sanctioned"),
        _ship(999000002, "TEST HULL B", watchlist="shadow_fleet"),
        _ship(999000003, "TEST HULL C"),
    ]
    _write_watch(data, [ongoing, ended, plain], ships)

    case = _freeze(data)

    assert case["pick"] == {
        "rule": 1,
        "reason": "Watchlisted ship went dark",
        "alert_id": "dark-999000001-1000",
    }


def test_listed_hull_without_could_meet_falls_through_to_rule_2(data: Path) -> None:
    # A listed hull with no could-meet contact cannot carry the story (Chief, 2026-11-22).
    listed_alone = _alert("dark-999000021-1000", 999000021, "TEST HULL D", status="ongoing", contacts=[])
    other = _alert(
        "dark-999000022-1000",
        999000022,
        "TEST HULL E",
        status="ongoing",
        contacts=[_contact(999000023, "TEST CONTACT 6", watchlist="sanctioned")],
    )
    ships = [
        _ship(999000021, "TEST HULL D", watchlist="sanctioned"),
        _ship(999000022, "TEST HULL E"),
    ]
    _write_watch(data, [listed_alone, other], ships)

    case = _freeze(data)

    assert case["pick"]["rule"] == 2
    assert case["pick"]["alert_id"] == "dark-999000022-1000"


def test_rule_2_picks_most_listed_could_meet_contacts(data: Path) -> None:
    alert_b = _alert(
        "dark-999000004-1000",
        999000004,
        "TEST HULL D",
        status="ended",
        contacts=[
            _contact(999000021, "TEST LISTED 1", watchlist="sanctioned"),
            _contact(999000022, "TEST LISTED 2", watchlist="shadow_fleet"),
            _contact(999000023, "TEST PLAIN 1"),
        ],
    )
    alert_c = _alert(
        "dark-999000005-1000",
        999000005,
        "TEST HULL E",
        status="ongoing",
        contacts=[_contact(999000024, "TEST LISTED 3", watchlist="sanctioned")],
    )
    ships = [
        _ship(999000004, "TEST HULL D"),
        _ship(999000005, "TEST HULL E"),
        _ship(999000021, "TEST LISTED 1"),
    ]
    _write_watch(data, [alert_b, alert_c], ships)

    case = _freeze(data)

    assert case["pick"] == {
        "rule": 2,
        "reason": "Most could-meet contacts on a sanctions list",
        "alert_id": "dark-999000004-1000",
    }
    dossiers = {row["mmsi"]: row["dossier"] for row in case["contacts"]}
    assert dossiers[999000021] is not None
    assert dossiers[999000023] is None


def test_rule_3_falls_back_to_could_meet_count(data: Path) -> None:
    alert_b = _alert(
        "dark-999000006-1000",
        999000006,
        "TEST HULL F",
        status="ended",
        contacts=[
            _contact(999000031, "TEST PLAIN 1"),
            _contact(999000032, "TEST PLAIN 2"),
            _contact(999000033, "TEST PLAIN 3"),
        ],
    )
    alert_c = _alert(
        "dark-999000007-1000",
        999000007,
        "TEST HULL G",
        status="ongoing",
        contacts=[
            _contact(999000034, "TEST PLAIN 4"),
            _contact(999000035, "TEST PLAIN 5"),
        ],
    )
    _write_watch(data, [alert_b, alert_c], [_ship(999000006, "TEST HULL F"), _ship(999000007, "TEST HULL G")])

    case = _freeze(data)

    assert case["pick"] == {
        "rule": 3,
        "reason": "Most could-meet contacts",
        "alert_id": "dark-999000006-1000",
    }


def test_missing_required_field_raises_with_the_alert_id(data: Path) -> None:
    broken = _alert("dark-999000008-1000", 999000008, "TEST HULL H")
    del broken["speed_cap_kn"]
    _write_watch(data, [broken], [_ship(999000008, "TEST HULL H")])

    with pytest.raises(ValueError, match="dark-999000008-1000"):
        _freeze(data)


# ---- case contents ----------------------------------------------------------


def test_background_keeps_only_near_valid_ships(data: Path) -> None:
    alert = _alert("dark-999000009-1000", 999000009, "TEST HULL I")
    ships = [
        _ship(999000009, "TEST HULL I"),
        _ship(999000041, "TEST NEAR VALID", lat=10.0, lon=20.1),
        _ship(999000042, "TEST NEAR NO VALID FLAG", valid=None, lat=10.0, lon=20.2),
        _ship(999000043, "TEST INVALID", valid=False, lat=10.0, lon=20.1),
        _ship(999000044, "TEST FAR", lat=30.0, lon=20.0),
    ]
    _write_watch(data, [alert], ships)

    case = _freeze(data)

    assert case["reach_km"] == pytest.approx(22.22)
    assert {row["mmsi"] for row in case["background"]} == {999000041, 999000042}
    near = next(row for row in case["background"] if row["mmsi"] == 999000041)
    assert near == {"mmsi": 999000041, "lat": 10.0, "lon": 20.1}


# ---- satcheck ---------------------------------------------------------------

BANNER = "Scenario: how an alert looks when a satellite pass catches a dark ship"
SUB = "Real radar pass, 21 Nov 05:15 UTC, over the Baltic. Not this ship."
PNG_A = b"\x89PNG\r\n\x1a\nFAKE-CHIP-A"
PNG_B = b"\x89PNG\r\n\x1a\nFAKE-CHIP-B"


def _write_scenario(data: Path, *, chips: tuple[str, ...] = ("001", "002")) -> Path:
    source = data / "satcheck" / freeze_case.SCENARIO_ALERT_ID
    source.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": "2026-11-21T05:20:00Z",
        "scene": {"time": "2026-11-21T05:15:51Z", "ships_seen": 3, "matched": 2},
        "chips": [
            {"chip_url": f"/api/satcheck/{freeze_case.SCENARIO_ALERT_ID}/{name}.png"} for name in chips
        ],
    }
    (data / "satcheck" / f"{freeze_case.SCENARIO_ALERT_ID}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (source / "001.png").write_bytes(PNG_A)
    (source / "002.png").write_bytes(PNG_B)
    return source


def test_scenario_satcheck_copies_payload_and_chips(data: Path) -> None:
    _write_scenario(data)
    _write_watch(
        data,
        [_alert("dark-999000010-1000", 999000010, "TEST HULL J")],
        [_ship(999000010, "TEST HULL J")],
    )

    case = _freeze(data)

    assert case["satcheck"] == {
        "mode": "scenario",
        "banner": BANNER,
        "sub": SUB,
        "payload_url": "/api/story/satcheck",
    }
    story_dir = data / "story_case"
    frozen = json.loads((story_dir / "satcheck.json").read_text(encoding="utf-8"))
    assert frozen["mode"] == "scenario"
    assert frozen["banner"] == BANNER
    assert frozen["sub"] == SUB
    urls = [chip["chip_url"] for chip in frozen["chips"]]
    assert urls == ["/api/story/satcheck/chip/001.png", "/api/story/satcheck/chip/002.png"]
    assert (story_dir / "chip" / "001.png").read_bytes() == PNG_A
    assert (story_dir / "chip" / "002.png").read_bytes() == PNG_B
    assert case["alert"]["evidence"]["minutes"] == 60


def test_scenario_satcheck_missing_chip_fails_loudly(data: Path) -> None:
    source = _write_scenario(data)
    (source / "002.png").unlink()
    _write_watch(
        data,
        [_alert("dark-999000010-1000", 999000010, "TEST HULL J")],
        [_ship(999000010, "TEST HULL J")],
    )

    with pytest.raises(FileNotFoundError):
        _freeze(data)


def test_real_satcheck_is_used_when_its_scene_is_inside_the_dark_window(data: Path) -> None:
    alert = _alert("dark-999000012-1000", 999000012, "TEST HULL K", t_end=None)
    _write_watch(data, [alert], [_ship(999000012, "TEST HULL K")])
    real = {"scene": {"time": "2026-11-21T15:00:00Z", "matched": 2}, "note": "TEST PASS"}
    (data / "satcheck" / "dark-999000012-1000.json").write_text(json.dumps(real), encoding="utf-8")

    case = _freeze(data)

    assert case["satcheck"] == {"mode": "real", "payload": real}


# ---- API --------------------------------------------------------------------


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(story.router)
    return TestClient(app)


def test_story_api_serves_the_frozen_case_and_chips(data: Path) -> None:
    _write_scenario(data)
    _write_watch(
        data,
        [_alert("dark-999000010-1000", 999000010, "TEST HULL J")],
        [_ship(999000010, "TEST HULL J")],
    )
    with _client() as client:
        assert client.get("/api/story").status_code == 404
        assert client.get("/api/story").json() == {"detail": "story case not frozen yet"}
        assert client.get("/api/story/satcheck").status_code == 404

    case = _freeze(data)

    with _client() as client:
        response = client.get("/api/story")
        assert response.status_code == 200
        assert response.json()["pick"] == case["pick"]
        assert response.headers["cache-control"] == "no-cache"

        satcheck = client.get("/api/story/satcheck")
        assert satcheck.status_code == 200
        assert satcheck.headers["cache-control"] == "no-cache"
        assert satcheck.json()["mode"] == "scenario"

        chip = client.get("/api/story/satcheck/chip/001.png")
        assert chip.status_code == 200
        assert chip.headers["content-type"] == "image/png"
        assert chip.content == PNG_A

        assert client.get("/api/story/satcheck/chip/..%2Fx.png").status_code == 404
        assert client.get("/api/story/satcheck/chip/01.png").status_code == 404
