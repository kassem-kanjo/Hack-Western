"""Prove scripts/build_watch.py turns recordings into the watch contract's one file.

The fixture is synthetic but laid out exactly like the recorder writes it: two region folders of
gzipped aisstream lines. The scenario pins the behaviours the screen depends on:

* a tanker that goes dark underway inside the Strait of Hormuz, with a stopped sanctioned hull
  nearby: one ongoing dark alert whose contacts include that hull with ``could_meet`` true;
* a moored hull that simply stops reporting: no alert, but counted in ``hidden_routine``;
* the precision gates from the real 12 h run (issue #86): dark alerts only for cargo, tanker
  and unknown-type hulls, contacts that must be stopped *and* offshore *and* outside a port
  anchorage to count as meetings, and sanctioned stops that must last 30 minutes;
* detection running on the recording's clock, so repeated builds produce the same ids.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

T0 = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_watch.py"

DARK = 273111111
SANCTIONED = 273222222
MOORED = 273333333
HORMUZ_FILLER = 273444444
BALTIC_FILLER = 273555555

SANCTIONED_IMO = 9876543

HORMUZ = "HORMUZ"
BALTIC = "BALTIC_WATCH"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_watch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through sys.modules, so register before executing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_watch = _load_script()


def _stamp(t: datetime) -> str:
    """aisstream's metadata spelling, including the nanoseconds the parser truncates."""
    return t.strftime("%Y-%m-%d %H:%M:%S") + f".{t.microsecond:06d}000 +0000 UTC"


def _position(
    mmsi: int,
    t: datetime,
    lat: float,
    lon: float,
    sog: float,
    *,
    cog: float = 90.0,
) -> dict[str, Any]:
    return {
        "MetaData": {"MMSI": mmsi, "time_utc": _stamp(t)},
        "MessageType": "PositionReport",
        "Message": {
            "PositionReport": {
                "Latitude": lat,
                "Longitude": lon,
                "Sog": sog,
                "Cog": cog,
                "TrueHeading": 90,
            }
        },
    }


def _static(mmsi: int, t: datetime, name: str, ship_type: int, imo: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"Name": name, "Type": ship_type}
    if imo is not None:
        body["ImoNumber"] = imo
    return {
        "MetaData": {"MMSI": mmsi, "time_utc": _stamp(t)},
        "MessageType": "ShipStaticData",
        "Message": {"ShipStaticData": body},
    }


def _minute(index: int, seconds: float = 0.0) -> datetime:
    return T0 + timedelta(minutes=index, seconds=seconds)


def _filler_lines(mmsi: int, lat: float, lon: float, first: int, last: int) -> list[dict[str, Any]]:
    """Enough traffic per minute that ``covered`` is satisfied (150 messages/minute).

    A dense, stationary hull stands in for the recorder's background traffic; it is what proves
    the receiver network stayed alive while the dark ship was silent.
    """
    lines: list[dict[str, Any]] = []
    for minute in range(first, last + 1):
        for index in range(150):
            lines.append(_position(mmsi, _minute(minute, index * 0.2), lat, lon, 0.0))
    return lines


def _write_region(root: Path, name: str, lines: list[dict[str, Any]]) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    with gzip.open(directory / "20261121T00.jsonl.gz", "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    return directory


def _write_ais(
    root: Path,
    *,
    dark_type: int = 80,
    contact_position: tuple[float, float] = (26.32, 56.28),
) -> Path:
    """Two regions: Hormuz (the dark scenario) and the Baltic (the routine one)."""
    ais = root / "ais"
    contact_lat, contact_lon = contact_position

    hormuz: list[dict[str, Any]] = [
        _static(DARK, _minute(0), "AFRA STAR", dark_type),
        _static(SANCTIONED, _minute(0), "GESTIA", 80, imo=SANCTIONED_IMO),
    ]
    for minute in range(66):
        # 12 kn north-east through the strait, then the transponder goes quiet.
        lat = 26.10 + 0.20 * minute / 65
        lon = 56.10 + 0.18 * minute / 65
        hormuz.append(_position(DARK, _minute(minute), lat, lon, 12.0))
    # The sanctioned tanker lies stopped for three hours at `contact_position`.
    for minute in range(0, 180, 5):
        hormuz.append(_position(SANCTIONED, _minute(minute), contact_lat, contact_lon, 0.4, cog=0.0))
    hormuz.extend(_filler_lines(HORMUZ_FILLER, 26.70, 56.70, 65, 179))
    _write_region(ais, HORMUZ, hormuz)

    baltic: list[dict[str, Any]] = [_static(MOORED, _minute(0), "MOORED ONE", 70)]
    for minute in range(0, 76, 5):
        baltic.append(_position(MOORED, _minute(minute), 54.50, 10.30, 0.2, cog=0.0))
    baltic.extend(_filler_lines(BALTIC_FILLER, 55.50, 12.50, 75, 179))
    _write_region(ais, BALTIC, baltic)
    return ais


def _write_sanctions(root: Path) -> Path:
    path = root / "maritime.csv"
    path.write_text(
        "type,caption,imo,mmsi,flag,countries,risk,aliases,url,datasets\n"
        f"VESSEL,GESTIA,{SANCTIONED_IMO},,,,sanctioned,,,\n",
        encoding="utf-8",
    )
    return path


def _build(root: Path, *, land: Path | None = None) -> dict[str, Any]:
    return build_watch.build(
        hours=12,
        ais_dir=root / "ais",
        out=root / "out",
        sanctions=root / "maritime.csv",
        land=land,
    )


def _write_land(root: Path, bbox: tuple[float, float, float, float]) -> Path:
    """A one-box land file, so the offshore gate is exercised without the real basemap."""
    min_lon, min_lat, max_lon, max_lat = bbox
    ring = [
        [min_lon, min_lat],
        [max_lon, min_lat],
        [max_lon, max_lat],
        [min_lon, max_lat],
        [min_lon, min_lat],
    ]
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }
    path = root / "land.geojson"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_dark_ship_has_one_ongoing_alert_inside_hormuz(tmp_path: Path) -> None:
    _write_ais(tmp_path)
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)
    dark_alerts = [alert for alert in payload["alerts"] if alert["kind"] == "dark"]

    assert len(dark_alerts) == 1
    alert = dark_alerts[0]
    assert alert["status"] == "ongoing"
    assert alert["mmsi"] == DARK
    assert alert["name"] == "AFRA STAR"
    assert alert["area_id"] == "HORMUZ_STRAIT"
    assert alert["label"] == "Went dark near sanctioned ship"
    assert alert["t_end"] is None
    assert alert["last"]["sog"] == 12.0
    assert alert["id"].startswith(f"dark-{DARK}-")
    assert alert["t_start"] == "2026-11-21T01:05:00Z"


def test_contacts_include_the_stopped_sanctioned_hull(tmp_path: Path) -> None:
    _write_ais(tmp_path)
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)
    alert = next(alert for alert in payload["alerts"] if alert["kind"] == "dark")
    contact = next(contact for contact in alert["contacts"] if contact["mmsi"] == SANCTIONED)

    assert contact["name"] == "GESTIA"
    assert contact["watchlist"] == "sanctioned"
    assert contact["stopped"] is True
    assert contact["could_meet"] is True


def test_moored_ship_is_hidden_not_alerted(tmp_path: Path) -> None:
    _write_ais(tmp_path)
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)

    assert payload["counts"]["hidden_routine"] >= 1
    assert all(alert["mmsi"] != MOORED for alert in payload["alerts"])


def test_ships_carry_watchlist_and_alert_ids(tmp_path: Path) -> None:
    _write_ais(tmp_path)
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)
    ships = {ship["mmsi"]: ship for ship in payload["ships"]}

    assert ships[SANCTIONED]["watchlist"] == "sanctioned"
    assert ships[DARK]["watchlist"] is None
    assert ships[DARK]["alert_ids"], "the dark ship's own alert must be linked back"
    assert ships[SANCTIONED]["alert_ids"], "the contact must carry the alert it appears in"


def test_contract_shape_and_areas(tmp_path: Path) -> None:
    _write_ais(tmp_path)
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)

    assert set(payload) == {"generated_at", "refresh_s", "areas", "ships", "alerts", "counts"}
    assert payload["refresh_s"] == 300
    assert set(payload["counts"]) == {"ships", "alerts", "hidden_routine"}
    kinds = {area["kind"] for area in payload["areas"]}
    assert kinds == {"listening", "watched"}
    ids = {area["id"] for area in payload["areas"]}
    assert "HORMUZ_STRAIT" in ids and "HORMUZ" in ids


def test_ids_are_stable_across_runs(tmp_path: Path) -> None:
    _write_ais(tmp_path)
    _write_sanctions(tmp_path)

    first = _build(tmp_path)
    second = _build(tmp_path)

    assert [alert["id"] for alert in first["alerts"]] == [alert["id"] for alert in second["alerts"]]


def test_passenger_ferry_going_dark_near_a_sanctioned_ship_is_hidden(tmp_path: Path) -> None:
    # Issue #86's real-run failure: a ferry with a long gap inherited "near shadow fleet ship"
    # because the reach disk contained the sanctioned hull. Passenger hulls are not dark news.
    _write_ais(tmp_path, dark_type=60)
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)

    assert all(alert["mmsi"] != DARK for alert in payload["alerts"])
    assert all(alert["kind"] != "dark" for alert in payload["alerts"])


def test_cargo_dark_near_a_sanctioned_tanker_stopped_offshore(tmp_path: Path) -> None:
    # Cargo hull, sanctioned tanker stopped in open water: the meeting survives the gates.
    _write_ais(tmp_path, dark_type=70)
    _write_sanctions(tmp_path)
    land = _write_land(tmp_path, (54.0, 24.0, 54.5, 24.4))

    payload = _build(tmp_path, land=land)
    dark_alerts = [alert for alert in payload["alerts"] if alert["kind"] == "dark"]

    assert len(dark_alerts) == 1
    alert = dark_alerts[0]
    assert alert["mmsi"] == DARK
    assert alert["label"] == "Went dark near sanctioned ship"
    contact = next(contact for contact in alert["contacts"] if contact["mmsi"] == SANCTIONED)
    assert contact["stopped"] is True
    assert contact["could_meet"] is True


def test_sanctioned_tanker_stopped_in_a_port_anchorage_is_hidden(tmp_path: Path) -> None:
    # Fujairah's anchorage: a sanctioned tanker waiting there is ordinary port business.
    _write_ais(tmp_path, contact_position=(25.20, 56.45))
    _write_sanctions(tmp_path)

    payload = _build(tmp_path)

    assert all(alert["kind"] != "sanctioned_stop" for alert in payload["alerts"])
    assert all(alert["mmsi"] != SANCTIONED for alert in payload["alerts"])


def test_contacts_inshore_are_listed_but_cannot_meet(tmp_path: Path) -> None:
    # A contact inside the reach disk is still shown, but a hull at the quay is not a meeting.
    _write_ais(tmp_path, contact_position=(26.35, 56.30))
    _write_sanctions(tmp_path)
    # Land covers the sanctioned hull's position exactly, so `offshore_km` is zero.
    land = _write_land(tmp_path, (56.25, 26.30, 56.35, 26.40))

    payload = _build(tmp_path, land=land)
    alert = next(alert for alert in payload["alerts"] if alert["kind"] == "dark")
    contact = next(contact for contact in alert["contacts"] if contact["mmsi"] == SANCTIONED)

    assert contact["stopped"] is True
    assert contact["could_meet"] is False
    assert alert["label"] == "Went dark"


def test_explain_flag_prints_removed_candidate_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_ais(tmp_path, dark_type=60)
    _write_sanctions(tmp_path)

    code = build_watch.main(
        [
            "--ais",
            str(tmp_path / "ais"),
            "--out",
            str(tmp_path / "out"),
            "--sanctions",
            str(tmp_path / "maritime.csv"),
            "--land",
            str(tmp_path / "missing.geojson"),
            "--explain",
        ]
    )

    printed = capsys.readouterr().out
    assert code == 0
    assert "explain dark:" in printed
    assert "removed type 1" in printed
