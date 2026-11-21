"""Build the V3 watch snapshot (issue #86) from every recorded AIS region.

    uv run python scripts/build_watch.py [--hours 12] [--loop 300]

``data/snapshot/watch.json`` is one file for the whole screen, not one per region: the watch
map is global in theory and shows every listening area at once, so this script reads *all*
recorded folders under ``data/cache/ais/`` and merges their findings.

Nothing here re-implements a detector. ``scripts/build_live.py`` already owns the bridge from a
recording to tracks, coverage and events, including the join decisions that belong to the
pipeline rather than to ``core/`` (class speed caps, clone-jump suppression, sanctions matching).
This script imports those helpers and adds only what the watch contract needs:

* ``core.watch`` open silences at the recording's latest minute (ships dark *now*), reach and
  possible contacts - the geometry the click-through already uses;
* the contract's suspicious-only filter, short labels, stable ``kind-mmsi-epoch`` ids and the
  routine count that keeps ordinary traffic out of the alert list;
* the ``sanctioned_stop`` rule, which needs the sanctions join and the anchorage table.

A run parses each hourly file at most once: parsed hours are cached by ``(path, mtime)`` for the
lifetime of the process, so ``--loop 300`` pays the parse cost only for the hour that just grew.

Pure joins, no new dependencies. Never reads ``.env``.
"""

# ruff: noqa: E402  (the sys.path fix below must run before the darkfleet imports)

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

# Running this file directly puts ``scripts/`` first on ``sys.path``, where the repo's own
# ``scripts/numbers.py`` shadows the standard-library module numpy imports at startup. Drop that
# entry: the only sibling this script needs (build_live.py) is loaded by explicit path below.
_SCRIPT_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0] or ".").resolve() == _SCRIPT_DIR:
    sys.path.pop(0)

from darkfleet.core import kinematics
from darkfleet.core.anchorages import context_for
from darkfleet.core.areas import LISTENING, WATCHED, area_of
from darkfleet.core.encounters import meetings
from darkfleet.core.geo import Fix
from darkfleet.core.speeds import max_speed_knots
from darkfleet.core.spoofing import filter_explained_jumps, ghost_points
from darkfleet.core.tracks import CoverageMinute, Event, Ping, VesselInfo, ship_class
from darkfleet.core.watch import in_reach, open_silences, possible_contacts
from darkfleet.core.water import load_land
from darkfleet.ingest.ais_tracks import coverage, iter_files
from darkfleet.ingest.watchlist import imo_watchlist


def _load_build_live() -> ModuleType:
    """Load the sibling pipeline script by path, so reuse works under any working directory.

    ``build_live.py`` is a CLI script rather than an installed module. Loading it explicitly is
    what lets this builder call the one existing implementation of the recording-to-events
    bridge instead of copying it; the module name is namespaced so a test that has already
    loaded ``build_live`` under another name cannot hand us a different object.
    """
    spec = importlib.util.spec_from_file_location(
        "darkfleet_watch_build_live", Path(__file__).resolve().with_name("build_live.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load scripts/build_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_build_live = _load_build_live()

CARGO_TANKER_TYPES = _build_live.CARGO_TANKER_TYPES
_iso = _build_live._iso
_last_hours = _build_live._last_hours
_meeting_context = _build_live._meeting_context
_merge_info = _build_live._merge_info
_run_silences = _build_live._run_silences
_write_json = _build_live._write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AIS = Path("data/cache/ais")
DEFAULT_OUT = Path("data/snapshot")
DEFAULT_LAND = Path("web/public/basemap/land.geojson")
DEFAULT_SANCTIONS = Path("data/sanctions/maritime.csv")

REFRESH_SECONDS = 300
"""The loop interval the UI advertises; also written to the snapshot as ``refresh_s``."""

SUSPICIOUS_CATEGORIES = frozenset({"sanctioned", "shadow_fleet"})
"""The two watchlist categories the contract exposes; detention is context, not an accusation."""

WATCHED_IDS = frozenset(area["id"] for area in WATCHED)
SHIP_WINDOW_HOURS = 2
"""Ships are drawn if heard within this many hours of the recording's latest minute."""

STOP_SOG_KN = 1.0
"""``sanctioned_stop`` threshold: a hull at or below this is stopped, not manoeuvring."""

MOORED_SOG_KN = 1.0
"""A quiet track whose last report is at or below this is moored, i.e. routine, not dark."""

SANCTIONED_STOP_SEVERITY = 0.6
"""Base severity before the watched-area and watchlist bumps raise it."""

MAX_CONTACTS = 20
"""The contract's contact cap; requested higher from ``possible_contacts`` so a watchlisted
contact further out is not dropped by the distance cut before the watchlist-first sort."""

MEETING_OFFSHORE_KM = 5.0
"""Matches ``build_live``: harbour-adjacent pairs are not rendezvous."""


@dataclass(frozen=True)
class _Region:
    """One recorded folder, already windowed to the last ``hours`` of its own recording."""

    name: str
    pings: list[Ping]
    vessels: dict[int, VesselInfo]


@dataclass
class _Scan:
    """Everything one detector pass produced for a region."""

    region: _Region
    tracks: dict[int, list[Ping]]
    rows: list[CoverageMinute]
    classes: dict[int, str]
    candidates: list[tuple[Event, str, str]]


_PARSED_HOURS: dict[Path, tuple[int, list[Ping | VesselInfo]]] = {}
"""Parsed hour files keyed by path, valued with the mtime they were parsed at.

``--loop`` runs in one process, so this is what makes a rerun cheap: only the hour file the
recorder is still appending to changes its mtime and is parsed again.
"""


def _parse_hour(path: Path) -> list[Ping | VesselInfo]:
    """Parse one ``*.jsonl.gz`` unless the file is unchanged since it was last parsed."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return []
    cached = _PARSED_HOURS.get(path)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    objects = list(iter_files([path]))
    _PARSED_HOURS[path] = (mtime_ns, objects)
    return objects


def _read_region(directory: Path) -> tuple[list[Ping], dict[int, VesselInfo]]:
    """Parse every hour file in a region folder, merging static data per MMSI."""
    pings: list[Ping] = []
    vessels: dict[int, VesselInfo] = {}
    for path in sorted(directory.glob("*.jsonl.gz")):
        for obj in _parse_hour(path):
            if isinstance(obj, Ping):
                pings.append(obj)
            else:
                vessels[obj.mmsi] = _merge_info(vessels.get(obj.mmsi), obj)
    return pings, vessels


def _region_dirs(ais_dir: Path) -> list[Path]:
    """Every recorded region folder, in name order so repeated runs agree."""
    try:
        return sorted(path for path in Path(ais_dir).iterdir() if path.is_dir())
    except OSError:
        return []


def _resolved(path: Path, fallback: Path) -> Path:
    """Use a folder as given when it exists, else the repo-relative default."""
    return path if Path(path).exists() else fallback


def _load_regions(ais_dir: Path, hours: int | None) -> list[_Region]:
    """Read every region that has pings, keeping the newest ``hours`` of each recording."""
    regions: list[_Region] = []
    for directory in _region_dirs(ais_dir):
        pings, vessels = _read_region(directory)
        pings = _last_hours(pings, hours)
        if pings:
            regions.append(_Region(name=directory.name, pings=pings, vessels=vessels))
    return regions


def _scan(region: _Region, now: datetime, land: Any, watch_rows: list[dict[str, Any]]) -> _Scan:
    """Run build_live's detectors over one region and tag each finding with its alert shape.

    Clone events are not a contract kind, so they are used only for what they explain: an MMSI
    with two transmitters cannot also have an impossible jump. The clone list itself is dropped
    after suppressing those jumps, which is the same join ``build_live`` makes.
    """
    rows = coverage(ping.t for ping in region.pings)
    tracks = kinematics.split_tracks(region.pings)
    classes = {
        mmsi: ship_class(region.vessels[mmsi].ship_type if mmsi in region.vessels else None)
        for mmsi in tracks
    }
    activity = kinematics.build_activity_index(tracks)

    candidates: list[tuple[Event, str, str]] = [
        (event, "dark", "ongoing") for event in open_silences(tracks, rows, now, activity=activity)
    ]
    candidates.extend((event, "dark", "ended") for event in _run_silences(tracks, rows))

    clones: list[Event] = []
    jumps: list[Event] = []
    for mmsi, track in tracks.items():
        clones.extend(kinematics.clones(track))
        jumps.extend(kinematics.jumps(track, max_speed_knots(classes.get(mmsi, "default"))))
    cloned = {event.mmsi for event in clones}
    jumps = [event for event in jumps if event.mmsi not in cloned]

    meeting_events = meetings(
        tracks,
        land=land,
        min_offshore_km=MEETING_OFFSHORE_KM,
        ship_types=CARGO_TANKER_TYPES,
        vessel_types={mmsi: info.ship_type for mmsi, info in region.vessels.items()},
    )
    meeting_events = _meeting_context(meeting_events, watch_rows)
    spoof_events = ghost_points(tracks, land=land)
    jumps = filter_explained_jumps(jumps, spoof_events)

    candidates.extend((event, "jump", "ended") for event in jumps)
    candidates.extend((event, "meeting", "ended") for event in meeting_events)
    candidates.extend((event, "spoof", "ended") for event in spoof_events)
    return _Scan(region=region, tracks=tracks, rows=rows, classes=classes, candidates=candidates)


def _last_ping(track: Sequence[Ping], when: datetime) -> Ping | None:
    """The newest fix at or before ``when``, the position the alert's ``last`` field shows."""
    best: Ping | None = None
    for ping in track:
        if ping.t <= when and (best is None or ping.t > best.t):
            best = ping
    return best


def _watchlisted_in_reach(
    event: Event,
    *,
    tracks: dict[int, list[Ping]],
    categories: dict[int, str],
    classes: dict[int, str],
    now: datetime,
) -> bool:
    """Cheap form of the contract's "involving a watchlisted ship" test for a dark alert.

    Scanning only watchlisted hulls (usually a few dozen) answers whether a full
    ``possible_contacts`` pass could discover one, so a dark gap in open water that is not
    otherwise suspicious never pays for the full pass over every ping.
    """
    if not categories:
        return False
    cap_kn = max_speed_knots(classes.get(event.mmsi, "default"))
    ping = _last_ping(tracks.get(event.mmsi, []), event.start)
    last = (
        Fix(lat=ping.lat, lon=ping.lon, t=ping.t)
        if ping is not None
        else Fix(lat=event.lat, lon=event.lon, t=event.start)
    )
    next_fix: Fix | None = None
    if event.end is not None:
        next_lat = event.evidence.get("next_lat")
        next_lon = event.evidence.get("next_lon")
        if isinstance(next_lat, (int, float)) and isinstance(next_lon, (int, float)):
            next_fix = Fix(lat=float(next_lat), lon=float(next_lon), t=event.end)
    t_end = event.end or now
    for mmsi in categories:
        if mmsi == event.mmsi:
            continue
        for other in tracks.get(mmsi, []):
            if other.t < event.start or other.t > t_end:
                continue
            if in_reach(last, next_fix, cap_kn, other.lat, other.lon, other.t):
                return True
    return False


def _contact_rows(
    dark_mmsi: int,
    last: Fix,
    next_fix: Fix | None,
    cap_kn: float,
    t_start: datetime,
    t_end: datetime,
    tracks: dict[int, list[Ping]],
    vessels: dict[int, VesselInfo],
    categories: dict[int, str],
) -> list[dict[str, Any]]:
    """Join ``possible_contacts`` with the static identity and watchlist, sanctioned-first.

    ``possible_contacts`` already caps its own list; asking for more than the contract's 20
    lets the watchlist reorder happen before the cut, so a sanctioned hull that sits a little
    further out is still shown.
    """
    contacts = possible_contacts(
        dark_mmsi,
        last,
        next_fix,
        cap_kn,
        t_start,
        t_end,
        tracks,
        max_contacts=MAX_CONTACTS * 5,
    )
    rows = [
        {
            "mmsi": contact.mmsi,
            "name": vessels[contact.mmsi].name if contact.mmsi in vessels else None,
            "watchlist": categories.get(contact.mmsi),
            "lat": contact.lat,
            "lon": contact.lon,
            "t": _iso(contact.t),
            "min_dist_km": contact.min_dist_km,
            "stopped": contact.stopped,
            "could_meet": contact.could_meet,
        }
        for contact in contacts
    ]
    rows.sort(
        key=lambda row: (row["watchlist"] is None, not row["could_meet"], row["min_dist_km"], row["mmsi"])
    )
    return rows[:MAX_CONTACTS]


def _raise_severity(base: float, *, watched: bool, involved: bool) -> float:
    """A watched area and a watchlisted hull both make a finding more worth showing."""
    severity = base
    if watched:
        severity += 0.2
    if involved:
        severity += 0.2
    return round(min(1.0, max(0.0, severity)), 3)


def _spoof_label(event: Event) -> str:
    """Short label from the ghost point's vessel count; the contract wants names, not prose."""
    try:
        n_vessels = int(event.evidence.get("n_vessels") or 1)
    except (TypeError, ValueError):
        n_vessels = 1
    return f"{n_vessels} ships, one false position" if n_vessels > 1 else "One ship, false position"


def _label(
    kind: str,
    event: Event,
    *,
    contacts: list[dict[str, Any]],
    categories: dict[int, str],
    classes: dict[int, str],
) -> str:
    """A label of at most six words, as approved: no paragraphs on the watch screen."""
    if kind == "dark":
        near = next((contact["watchlist"] for contact in contacts if contact["watchlist"]), None)
        if near is not None:
            return f"Went dark near {near.replace('_', ' ')} ship"
        if categories.get(event.mmsi) is not None:
            return "Watchlisted ship went dark"
        return "Went dark"
    if kind == "spoof":
        return _spoof_label(event)
    if kind == "jump":
        return "Impossible jump"
    if kind == "meeting":
        involved = event.other_mmsi in categories or event.mmsi in categories
        return "Meeting near watchlisted ship" if involved else "Ships met"
    cls = classes.get(event.mmsi) or "default"
    return f"Sanctioned {cls} stopped" if cls != "default" else "Sanctioned ship stopped"


def _alert_payload(
    event: Event,
    kind: str,
    status: str,
    *,
    vessels: dict[int, VesselInfo],
    categories: dict[int, str],
    classes: dict[int, str],
    tracks: dict[int, list[Ping]],
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    """One contract-shaped alert plus whether a watchlisted hull is involved somewhere in it.

    The involvement flag is returned rather than stored in the JSON: the contract has no such
    field, and the UI reads the same fact from ``contacts[].watchlist`` / the ship row.
    """
    mmsi = event.mmsi
    cap_kn = round(max_speed_knots(classes.get(mmsi, "default")), 3)
    contacts: list[dict[str, Any]] = []
    next_fix: Fix | None = None

    last_fix = _last_ping(tracks.get(mmsi, []), event.start)
    if kind == "dark":
        if event.end is not None:
            next_lat = event.evidence.get("next_lat")
            next_lon = event.evidence.get("next_lon")
            if isinstance(next_lat, (int, float)) and isinstance(next_lon, (int, float)):
                next_fix = Fix(lat=float(next_lat), lon=float(next_lon), t=event.end)
        fixed = last_fix or Fix(lat=event.lat, lon=event.lon, t=event.start)
        contacts = _contact_rows(
            mmsi,
            fixed,
            next_fix,
            cap_kn,
            event.start,
            event.end or now,
            tracks,
            vessels,
            categories,
        )

    if last_fix is not None and kind == "dark":
        last = {
            "lat": last_fix.lat,
            "lon": last_fix.lon,
            "t": _iso(last_fix.t),
            "sog": last_fix.sog,
            "cog": last_fix.cog,
        }
    else:
        last = {
            "lat": event.lat,
            "lon": event.lon,
            "t": _iso(event.start),
            "sog": None,
            "cog": None,
        }

    involved = categories.get(mmsi) is not None
    if event.other_mmsi is not None and categories.get(event.other_mmsi) is not None:
        involved = True
    if any(contact["watchlist"] for contact in contacts):
        involved = True
    if kind == "spoof":
        for row in event.evidence.get("vessels", []):
            if isinstance(row, dict) and categories.get(row.get("mmsi")) is not None:
                involved = True

    area_id = area_of(last["lat"], last["lon"])
    watched = area_id in WATCHED_IDS and kind in {"dark", "meeting", "sanctioned_stop"}
    payload = {
        "id": f"{kind}-{mmsi}-{int(event.start.timestamp())}",
        "kind": kind,
        "status": status,
        "mmsi": mmsi,
        "name": vessels[mmsi].name if mmsi in vessels else None,
        "area_id": area_id,
        "severity": _raise_severity(event.severity, watched=watched, involved=involved),
        "label": _label(kind, event, contacts=contacts, categories=categories, classes=classes),
        "t_start": _iso(event.start),
        "t_end": _iso(event.end) if event.end is not None else None,
        "last": last,
        "next": {"lat": next_fix.lat, "lon": next_fix.lon, "t": _iso(next_fix.t)} if next_fix else None,
        "speed_cap_kn": cap_kn,
        "contacts": contacts,
        "evidence": event.evidence,
    }
    return payload, involved


def _sanctioned_stops(scan: _Scan, now: datetime, categories: dict[int, str]) -> list[dict[str, Any]]:
    """Watchlisted hulls stopped outside a routine anchorage during the window.

    The anchorage test is the whole point: a sanctioned tanker waiting at Fujairah or Singapore
    is ordinary port business, while the same hull stopped in open water is the behaviour the
    watch screen exists to surface.
    """
    payloads: list[dict[str, Any]] = []
    for mmsi, category in sorted(categories.items()):
        track = scan.tracks.get(mmsi)
        if not track:
            continue
        stopped = [
            ping
            for ping in track
            if ping.sog is not None and ping.sog <= STOP_SOG_KN and context_for(ping.lat, ping.lon) is None
        ]
        if not stopped:
            continue
        ping = max(stopped, key=lambda item: item.t)
        latest = max(track, key=lambda item: item.t)
        ongoing = latest.t == ping.t and (now - ping.t) <= timedelta(minutes=30)
        cls = scan.classes.get(mmsi) or "default"
        area_id = area_of(ping.lat, ping.lon)
        severity = _raise_severity(
            SANCTIONED_STOP_SEVERITY,
            watched=area_id in WATCHED_IDS,
            involved=category in SUSPICIOUS_CATEGORIES,
        )
        label = f"Sanctioned {cls} stopped" if cls != "default" else "Sanctioned ship stopped"
        payloads.append(
            {
                "id": f"sanctioned_stop-{mmsi}-{int(ping.t.timestamp())}",
                "kind": "sanctioned_stop",
                "status": "ongoing" if ongoing else "ended",
                "mmsi": mmsi,
                "name": scan.region.vessels[mmsi].name if mmsi in scan.region.vessels else None,
                "area_id": area_id,
                "severity": severity,
                "label": label,
                "t_start": _iso(ping.t),
                "t_end": None if ongoing else _iso(ping.t),
                "last": {
                    "lat": ping.lat,
                    "lon": ping.lon,
                    "t": _iso(ping.t),
                    "sog": ping.sog,
                    "cog": ping.cog,
                },
                "next": None,
                "speed_cap_kn": round(max_speed_knots(cls), 3),
                "contacts": [],
                "evidence": {"sog": ping.sog, "class": cls, "anchorage": None},
            }
        )
    return payloads


def _is_suspicious(payload: dict[str, Any], involved: bool) -> bool:
    """The contract's rule, in one place: what the alert list is allowed to contain."""
    if payload["kind"] == "spoof":
        return True
    if involved:
        return True
    return payload["kind"] in {"dark", "meeting", "sanctioned_stop"} and payload["area_id"] in WATCHED_IDS


def _routine_quiet(
    tracks: dict[int, list[Ping]],
    rows: Sequence[CoverageMinute],
    now: datetime,
    alerted: set[int],
) -> int:
    """Open gaps that are not dark: the ship was moored when it stopped reporting.

    ``open_silences`` deliberately ignores these, because a hull that goes quiet at the quay is
    not a finding. They still exist, so the one number the contract exposes for "we looked and
    left it out" counts them, but only when the receiver network was healthy for the whole gap.
    """
    count = 0
    for mmsi, track in tracks.items():
        if not track or mmsi in alerted:
            continue
        last = max(track, key=lambda ping: ping.t)
        minutes = (now - last.t).total_seconds() / 60.0
        if minutes <= 60.0:
            continue
        if last.sog is None or last.sog > MOORED_SOG_KN:
            continue
        if kinematics.covered(last.t, now, rows):
            count += 1
    return count


def _ships(
    tracks: dict[int, list[Ping]],
    vessels: dict[int, VesselInfo],
    categories: dict[int, str],
    alert_ids: dict[int, list[str]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Latest position of every hull heard in the last two hours, alert ids attached."""
    cutoff = now - timedelta(hours=SHIP_WINDOW_HOURS)
    ships: list[dict[str, Any]] = []
    for mmsi in sorted(tracks):
        track = tracks[mmsi]
        if not track:
            continue
        last = max(track, key=lambda ping: ping.t)
        if last.t < cutoff:
            continue
        info = vessels.get(mmsi)
        ships.append(
            {
                "mmsi": mmsi,
                "name": info.name if info else None,
                "lat": last.lat,
                "lon": last.lon,
                "t": _iso(last.t),
                "sog": last.sog,
                "class": ship_class(info.ship_type if info else None),
                "watchlist": categories.get(mmsi),
                "alert_ids": alert_ids.get(mmsi, []),
            }
        )
    return ships


def build(
    *,
    hours: int | None = 12,
    ais_dir: Path = DEFAULT_AIS,
    out: Path = DEFAULT_OUT,
    sanctions: Path | None = DEFAULT_SANCTIONS,
    land: Path | None = DEFAULT_LAND,
) -> dict[str, Any]:
    """Read every region, write ``watch.json``, and return the payload."""
    regions = _load_regions(Path(ais_dir), hours)
    latest = max((max(ping.t for ping in region.pings) for region in regions), default=None)
    now = (latest or datetime.now(UTC)).replace(second=0, microsecond=0)

    vessels: dict[int, VesselInfo] = {}
    for region in regions:
        for mmsi, info in region.vessels.items():
            vessels[mmsi] = _merge_info(vessels.get(mmsi), info)

    sanctions_path = Path(sanctions) if sanctions is not None else None
    cases = (
        imo_watchlist(list(vessels.values()), sanctions_path)
        if sanctions_path is not None and sanctions_path.is_file()
        else {}
    )
    categories = {
        mmsi: str(case["category"])
        for mmsi, case in cases.items()
        if case.get("category") in SUSPICIOUS_CATEGORIES
    }
    watch_rows = [{"mmsi": mmsi, "category": category} for mmsi, category in categories.items()]

    land_geom = load_land(land) if land is not None and Path(land).is_file() else None
    scans = [_scan(region, now, land_geom, watch_rows) for region in regions]
    all_tracks = kinematics.split_tracks([ping for region in regions for ping in region.pings])

    alerts: list[dict[str, Any]] = []
    alerted: set[int] = set()
    seen: set[str] = set()
    hidden = 0

    for scan in scans:
        for event, kind, status in scan.candidates:
            if kind == "dark":
                own = categories.get(event.mmsi) is not None
                if not own and area_of(event.lat, event.lon) not in WATCHED_IDS:
                    if not _watchlisted_in_reach(
                        event,
                        tracks=all_tracks,
                        categories=categories,
                        classes=scan.classes,
                        now=now,
                    ):
                        hidden += 1
                        continue
            payload, involved = _alert_payload(
                event,
                kind,
                status,
                vessels=vessels,
                categories=categories,
                classes=scan.classes,
                tracks=all_tracks,
                now=now,
            )
            if not _is_suspicious(payload, involved):
                hidden += 1
                continue
            if payload["id"] in seen:
                continue
            seen.add(payload["id"])
            alerts.append(payload)
            alerted.add(payload["mmsi"])
            alerted.update(contact["mmsi"] for contact in payload["contacts"])
        for payload in _sanctioned_stops(scan, now, categories):
            if payload["id"] in seen:
                continue
            seen.add(payload["id"])
            alerts.append(payload)
            alerted.add(payload["mmsi"])

    alerts.sort(key=lambda alert: (-alert["severity"], alert["id"]))
    alert_ids: dict[int, list[str]] = {}
    for alert in alerts:
        for mmsi in {alert["mmsi"]} | {contact["mmsi"] for contact in alert["contacts"]}:
            alert_ids.setdefault(mmsi, [])
            if alert["id"] not in alert_ids[mmsi]:
                alert_ids[mmsi].append(alert["id"])
    for mmsi in alert_ids:
        alert_ids[mmsi].sort()

    hidden += sum(_routine_quiet(scan.tracks, scan.rows, now, alerted) for scan in scans if scan.tracks)

    ships = _ships(all_tracks, vessels, categories, alert_ids, now)
    payload = {
        # The build's own clock, not the recording's: the SSE client compares this value to
        # decide a new file is worth refetching, so it must change on every run.
        "generated_at": _iso(datetime.now(UTC).replace(microsecond=0)),
        "refresh_s": REFRESH_SECONDS,
        "areas": [dict(area) for area in [*LISTENING, *WATCHED]],
        "ships": ships,
        "alerts": alerts,
        "counts": {"ships": len(ships), "alerts": len(alerts), "hidden_routine": hidden},
    }
    _write_json(Path(out) / "watch.json", payload)
    return payload


def _generated_at(payload: dict[str, Any]) -> str:
    """The snapshot's own timestamp, or the run's clock if the field is missing."""
    return str(payload.get("generated_at") or _iso(datetime.now(UTC)))


def run_once(
    *,
    hours: int | None,
    ais_dir: Path,
    out: Path,
    sanctions: Path | None,
    land: Path | None,
) -> dict[str, Any]:
    """One build plus the single log line ``--loop`` prints per iteration."""
    started = time.perf_counter()
    payload = build(hours=hours, ais_dir=ais_dir, out=out, sanctions=sanctions, land=land)
    elapsed = time.perf_counter() - started
    counts = payload["counts"]
    print(
        f"watch {_generated_at(payload)}: {counts['alerts']} alerts, "
        f"{counts['hidden_routine']} hidden routine, {counts['ships']} ships in {elapsed:.1f}s",
        flush=True,
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the global watch snapshot from recordings.")
    parser.add_argument("--hours", type=int, default=12, help="newest N hours of each recording")
    parser.add_argument("--loop", type=int, default=0, help="re-run every N seconds, forever")
    parser.add_argument("--ais", type=Path, default=DEFAULT_AIS, help="recordings root")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot output folder")
    parser.add_argument("--land", type=Path, default=DEFAULT_LAND, help="land GeoJSON")
    parser.add_argument("--sanctions", type=Path, default=DEFAULT_SANCTIONS, help="sanctions CSV")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run once, or forever on ``--loop``; a missing recording root is a loud error."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be a positive integer")
    if args.loop < 0:
        parser.error("--loop must be zero or positive")

    ais_dir = _resolved(args.ais, ROOT / DEFAULT_AIS)
    if not ais_dir.is_dir():
        print(f"missing recordings folder: {ais_dir}", file=sys.stderr)
        return 2

    options = {
        "hours": args.hours,
        "ais_dir": ais_dir,
        "out": args.out,
        "sanctions": args.sanctions,
        "land": args.land,
    }
    if args.loop == 0:
        run_once(**options)
        return 0
    try:
        while True:
            run_once(**options)
            time.sleep(args.loop)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
