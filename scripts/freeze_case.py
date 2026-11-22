"""Freeze the demo story case (issue #274).

    uv run python scripts/freeze_case.py [--data-dir DIR] [--out PATH] [--now ISO]

The demo needs one alert it can tell a whole story around: the ship, its last fix, its trail,
the ships that could have met it, and the satellite pass the UI will show. Choosing that alert
is a judgement call, so it lives here as three explicit rules rather than in the web app. The
result is one frozen file, ``story_case.json``, plus the scenario satellite payload and its
chips under ``story_case/``.

Everything is read from the snapshot. The dossier is not rebuilt: ``api.vessels.get_vessel``
already owns that join, so this script sets ``DARKFLEET_DATA`` before calling it exactly as a
request would.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from darkfleet.api.vessels import get_vessel

LISTED = {"sanctioned", "shadow_fleet"}
"""Watchlist categories that count as a sanctions designation for the story rules."""

SCENARIO_ALERT_ID = "dark-636023657-1795299631"
"""BALTIC STAR's real satellite pass, used when the picked alert has no pass of its own."""

SCENARIO_BANNER = "Scenario: how an alert looks when a satellite pass catches a dark ship"
SCENARIO_SUB = "Real radar pass, 21 Nov 05:15 UTC, over the Baltic. Not this ship."

CHIP_URL_PREFIX = "/api/story/satcheck/chip"
EARTH_RADIUS_KM = 6371.0
"""Same sphere the web's ``distanceKm`` uses, so reach and background agree with the map."""

ALERT_FIELDS = (
    "id",
    "kind",
    "status",
    "mmsi",
    "name",
    "label",
    "area_id",
    "t_start",
    "t_end",
    "last",
    "speed_cap_kn",
    "trail",
    "evidence",
)


class NoDarkAlert(Exception):
    """watch.json has no dark alert to tell a story about."""


# ---- small pure helpers ------------------------------------------------------


def _number(value: Any) -> float | None:
    """A finite JSON number, or None (booleans are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _iso(value: datetime) -> str:
    """UTC ISO-8601 with the trailing ``Z`` every snapshot field uses."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    """Parse a snapshot timestamp, or None when it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_now(value: str) -> datetime:
    """Parse ``--now``; a naive value is UTC, like every stamp in the snapshot."""
    parsed = _parse_iso(value)
    if parsed is None:
        raise ValueError(f"invalid --now value: {value!r}")
    return parsed


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km on the 6371 km sphere."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _missing_fields(alert: dict[str, Any]) -> list[str]:
    """The required candidate fields this alert lacks, named for the error message."""
    missing: list[str] = []
    if not isinstance(alert.get("last"), dict):
        missing.append("last")
    if _number(alert.get("speed_cap_kn")) is None:
        missing.append("speed_cap_kn")
    if not isinstance(alert.get("trail"), list):
        missing.append("trail")
    evidence = alert.get("evidence")
    if not isinstance(evidence, dict) or _number(evidence.get("minutes")) is None:
        missing.append("evidence.minutes")
    return missing


def _could_meet(alert: dict[str, Any]) -> int:
    """How many of the alert's contacts could have met the hull."""
    contacts = alert.get("contacts")
    if not isinstance(contacts, list):
        return 0
    return sum(1 for row in contacts if isinstance(row, dict) and row.get("could_meet"))


def _listed_could_meet(alert: dict[str, Any]) -> int:
    """How many of those could-meet contacts are themselves on a listed watchlist."""
    contacts = alert.get("contacts")
    if not isinstance(contacts, list):
        return 0
    return sum(
        1
        for row in contacts
        if isinstance(row, dict) and row.get("could_meet") and row.get("watchlist") in LISTED
    )


def _mmsi(alert: dict[str, Any]) -> int:
    """The sort key's deterministic tail; a malformed MMSI sorts first."""
    value = alert.get("mmsi")
    return value if isinstance(value, int) else 0


def _ongoing_rank(alert: dict[str, Any]) -> int:
    """Ongoing alerts before ended ones."""
    return 0 if alert.get("status") == "ongoing" else 1


def _pick(rule: int, reason: str, alert: dict[str, Any]) -> dict[str, Any]:
    return {"rule": rule, "reason": reason, "alert_id": alert.get("id")}


# ---- selection ---------------------------------------------------------------


def select_pick(alerts: list[Any], ships: list[Any]) -> dict[str, Any]:
    """Apply the three rules in order and return the frozen pick.

    Candidates are dark alerts only. A candidate missing a field the story needs is an error,
    not something to skip: a half-told story is worse than a loud build failure.
    """
    dark = [row for row in alerts if isinstance(row, dict) and row.get("kind") == "dark"]
    if not dark:
        raise NoDarkAlert("no dark alert in watch.json")
    for alert in dark:
        missing = _missing_fields(alert)
        if missing:
            raise ValueError(f"dark alert {alert.get('id')} missing required field(s): {', '.join(missing)}")

    ship_rows = {row.get("mmsi"): row for row in ships if isinstance(row, dict)}

    # A listed hull only tells a story when at least one ship could have met it; a listed
    # hull with no could-meet contact falls through to rule 2 (Chief, 2026-11-22).
    listed_hulls = [
        alert
        for alert in dark
        if (ship_rows.get(alert.get("mmsi")) or {}).get("watchlist") in LISTED and _could_meet(alert) >= 1
    ]
    if listed_hulls:
        best = min(listed_hulls, key=lambda a: (_ongoing_rank(a), -_could_meet(a), _mmsi(a)))
        return _pick(1, "Watchlisted ship went dark", best)

    listed_contacts = [_listed_could_meet(alert) for alert in dark]
    if max(listed_contacts) > 0:
        best = min(dark, key=lambda a: (-_listed_could_meet(a), _ongoing_rank(a), _mmsi(a)))
        return _pick(2, "Most could-meet contacts on a sanctions list", best)

    best = min(dark, key=lambda a: (-_could_meet(a), _ongoing_rank(a), _mmsi(a)))
    return _pick(3, "Most could-meet contacts", best)


# ---- satcheck ----------------------------------------------------------------


def _read_json(path: Path) -> Any:
    """Parse one JSON file; a missing file is the caller's decision, not a silent {}."""
    return json.loads(path.read_text(encoding="utf-8"))


def _chip_names(node: Any) -> list[str]:
    """Every ``chip_url`` value in the payload, in document order."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "chip_url" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_chip_names(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_chip_names(value))
    return found


def _rewrite_chip_urls(node: Any) -> Any:
    """Point every ``chip_url`` at this API's chip route, keeping its file name."""
    if isinstance(node, dict):
        return {
            key: (
                f"{CHIP_URL_PREFIX}/{Path(value).name}"
                if key == "chip_url" and isinstance(value, str)
                else _rewrite_chip_urls(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_chip_urls(value) for value in node]
    return node


def _chip_stem(name: str) -> str | None:
    """``001.png`` -> ``001``; anything that is not a three-digit chip -> None."""
    base = Path(name).name
    stem = base[:-4] if base.endswith(".png") else ""
    return stem if len(stem) == 3 and stem.isdigit() else None


def _scenario_payload(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Copy the real BALTIC STAR pass and its chips, clearly labelled as a scenario."""
    source_json = data_dir / "satcheck" / f"{SCENARIO_ALERT_ID}.json"
    if not source_json.is_file():
        raise FileNotFoundError(f"scenario satcheck missing: {source_json}")
    source = _read_json(source_json)
    if not isinstance(source, dict):
        raise ValueError(f"scenario satcheck must be an object: {source_json}")

    source_chips = data_dir / "satcheck" / SCENARIO_ALERT_ID
    if not source_chips.is_dir():
        raise FileNotFoundError(f"scenario chip folder missing: {source_chips}")
    available = sorted(path for path in source_chips.glob("*.png") if _chip_stem(path.name) is not None)
    if not available:
        raise FileNotFoundError(f"scenario satcheck has no chip PNGs: {source_chips}")

    for url in _chip_names(source):
        stem = _chip_stem(url)
        if stem is None or not (source_chips / f"{stem}.png").is_file():
            raise FileNotFoundError(f"scenario chip missing for {url!r}")

    chip_out = out_dir / "story_case" / "chip"
    chip_out.mkdir(parents=True, exist_ok=True)
    for path in available:
        shutil.copyfile(path, chip_out / path.name)

    frozen = _rewrite_chip_urls(source)
    frozen["mode"] = "scenario"
    frozen["banner"] = SCENARIO_BANNER
    frozen["sub"] = SCENARIO_SUB
    _atomic_write_json(out_dir / "story_case" / "satcheck.json", frozen)
    return {
        "mode": "scenario",
        "banner": SCENARIO_BANNER,
        "sub": SCENARIO_SUB,
        "payload_url": "/api/story/satcheck",
    }


def _satcheck(data_dir: Path, out_dir: Path, alert: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Use the picked alert's own pass when one exists inside its dark window, else the scenario."""
    case_id = alert.get("id")
    real_path = data_dir / "satcheck" / f"{case_id}.json"
    if real_path.is_file():
        payload = _read_json(real_path)
        scene = payload.get("scene") if isinstance(payload, dict) else None
        when = _parse_iso(scene.get("time")) if isinstance(scene, dict) else None
        start = _parse_iso(alert.get("t_start"))
        end = _parse_iso(alert.get("t_end")) or now
        if when is not None and start is not None and start <= when <= end:
            return {"mode": "real", "payload": payload}
    return _scenario_payload(data_dir, out_dir)


# ---- case assembly -----------------------------------------------------------


def _dossier(mmsi: Any) -> dict[str, Any] | None:
    """``get_vessel`` for one hull; a 404 is a legitimate "no dossier", anything else is not."""
    if not isinstance(mmsi, int):
        return None
    try:
        return get_vessel(mmsi)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write through a sibling tmp file and replace, so a reader never sees half a case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def freeze(data_dir: Path, out_path: Path, now: datetime) -> dict[str, Any]:
    """Pick the alert, join the hulls and write the frozen case; return the written payload."""
    data_dir = Path(data_dir)
    out_path = Path(out_path)
    # get_vessel resolves the snapshot on every call, exactly as a request does.
    os.environ["DARKFLEET_DATA"] = str(data_dir)

    watch_path = data_dir / "watch.json"
    if not watch_path.is_file():
        raise FileNotFoundError(f"watch snapshot missing: {watch_path}")
    watch = _read_json(watch_path)
    if not isinstance(watch, dict):
        raise ValueError(f"watch snapshot must be an object: {watch_path}")

    alerts = watch.get("alerts") if isinstance(watch.get("alerts"), list) else []
    ships = watch.get("ships") if isinstance(watch.get("ships"), list) else []
    pick = select_pick(alerts, ships)
    alert = next(row for row in alerts if isinstance(row, dict) and row.get("id") == pick["alert_id"])

    speed_cap_kn = float(alert["speed_cap_kn"])
    minutes = float(alert["evidence"]["minutes"])
    reach_km = round(speed_cap_kn * 1.852 * minutes / 60, 2)

    alert_mmsi = alert.get("mmsi")
    ship_row = next((row for row in ships if isinstance(row, dict) and row.get("mmsi") == alert_mmsi), {})
    case_mmsis = {alert_mmsi}
    contacts: list[dict[str, Any]] = []
    for contact in alert.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        row = dict(contact)
        listed = contact.get("watchlist") in LISTED
        row["dossier"] = _dossier(contact.get("mmsi")) if listed else None
        contacts.append(row)
        case_mmsis.add(contact.get("mmsi"))

    background: list[dict[str, Any]] = []
    last = alert.get("last") if isinstance(alert.get("last"), dict) else {}
    anchor_lat, anchor_lon = _number(last.get("lat")), _number(last.get("lon"))
    if anchor_lat is not None and anchor_lon is not None:
        limit_km = 1.5 * reach_km
        for row in ships:
            if not isinstance(row, dict) or row.get("valid") is False:
                continue
            mmsi = row.get("mmsi")
            if mmsi in case_mmsis:
                continue
            lat, lon = _number(row.get("lat")), _number(row.get("lon"))
            if lat is None or lon is None:
                continue
            if _haversine_km(anchor_lat, anchor_lon, lat, lon) <= limit_km:
                background.append({"mmsi": mmsi, "lat": round(lat, 5), "lon": round(lon, 5)})

    satcheck = _satcheck(data_dir, out_path.parent, alert, now)

    case = {
        "frozen_at": _iso(now),
        "watch_generated_at": watch.get("generated_at"),
        "pick": pick,
        "alert": {key: alert.get(key) for key in ALERT_FIELDS},
        "reach_km": reach_km,
        "ship": {
            "mmsi": alert_mmsi,
            "name": ship_row.get("name") or alert.get("name"),
            "watchlist": ship_row.get("watchlist"),
            "dossier": _dossier(alert_mmsi),
        },
        "contacts": contacts,
        "background": background,
        "satcheck": satcheck,
    }
    _atomic_write_json(out_path, case)
    return case


# ---- CLI ---------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze the demo story case from watch.json.")
    parser.add_argument("--data-dir", help="snapshot directory (default: DARKFLEET_DATA or data/snapshot)")
    parser.add_argument("--out", help="case JSON path (default: <data-dir>/story_case.json)")
    parser.add_argument("--now", help="freeze time as ISO-8601 (default: current UTC)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = Path(args.data_dir or os.environ.get("DARKFLEET_DATA", "").strip() or "data/snapshot")
    out_path = Path(args.out) if args.out else data_dir / "story_case.json"
    now = parse_now(args.now) if args.now else datetime.now(UTC)

    try:
        case = freeze(data_dir, out_path, now)
    except NoDarkAlert as exc:
        print(f"freeze_case: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"freeze_case: {exc}", file=sys.stderr)
        return 1

    pick = case["pick"]
    print(
        f"rule {pick['rule']} | {pick['reason']} | {pick['alert_id']} "
        f"| satcheck {case['satcheck']['mode']} | {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
