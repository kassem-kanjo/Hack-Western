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
* the precision gates tuned on the 12 h recording: dark alerts only for cargo, tanker and
  unknown-type hulls; a contact only counts as ``could_meet`` when its representative ping was
  stopped, offshore and outside a routine anchorage; ``sanctioned_stop`` only for stops that
  lasted 30 minutes and happened offshore or inside a watched area;
* the same edge-case gates ``core.watch`` applies to ongoing gaps, applied to *ended* ones
  too: no alert for an identity that cannot be a real MMSI, and none for a hull whose last fix
  was leaving its listening box. A dark hull's ``speed_cap_kn`` follows its own track
  (``effective_cap_kn``), not only the class it filed, and its contacts use that cap;
* the ``recording`` freshness block: the newest fix, how many seconds old it is on the build's
  clock, and every receiver outage longer than three minutes, so the UI can say "last update
  N min ago" and draw the holes honestly;
* the ``sanctioned_stop`` rule, which needs the sanctions join and the anchorage table;
* the ``trail`` each alert carries (the ship's last six hours of fixes, capped at 60 points);
* the ``radar_unmatched`` alert, read from ``data/snapshot/hormuz_unseen.json`` and always
  credited "Powered by Global Fishing Watch"; a missing file simply adds no radar alert.

``--explain`` prints, per alert kind, how many candidates each of those rules removed without
changing the snapshot.

A run parses each hourly file at most once: parsed hours are cached by ``(path, mtime)`` for the
lifetime of the process, so ``--loop 300`` pays the parse cost only for the hour that just grew.

Pure joins, no new dependencies. Never reads ``.env``.
"""

# ruff: noqa: E402  (the sys.path fix below must run before the darkfleet imports)

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from types import ModuleType
from typing import Any

# Running this file directly puts ``scripts/`` first on ``sys.path``, so any sibling named like a
# standard-library module shadows it (we had ``scripts/numbers.py``, since renamed pitch_numbers.py,
# which broke numpy/decimal). Kept as a guard: the only sibling this script needs (build_live.py) is
# loaded by explicit path below.
_SCRIPT_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0] or ".").resolve() == _SCRIPT_DIR:
    sys.path.pop(0)

from darkfleet.core import kinematics
from darkfleet.core.anchorages import context_for
from darkfleet.core.areas import LISTENING, WATCHED, area_of
from darkfleet.core.encounters import meetings
from darkfleet.core.geo import Fix, distance_km
from darkfleet.core.speeds import max_speed_knots
from darkfleet.core.spoofing import filter_explained_jumps, ghost_points
from darkfleet.core.tracks import CoverageMinute, Event, Ping, VesselInfo, ship_class
from darkfleet.core.watch import (
    MIN_OFFSHORE_KM,
    contact_could_meet,
    dark_type_allowed,
    effective_cap_kn,
    in_reach,
    left_coverage,
    offshore_km,
    open_silences,
    possible_contacts,
    valid_mmsi,
)
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AIS = Path("data/cache/ais")
DEFAULT_OUT = Path("data/snapshot")
DEFAULT_LAND = Path("web/public/basemap/land.geojson")
DEFAULT_SANCTIONS = Path("data/sanctions/maritime.csv")
DEFAULT_UNSEEN = Path("data/snapshot/hormuz_unseen.json")
"""Radar-vs-AIS snapshot produced by ``scripts/build_unseen_fleet.py``."""

RADAR_AREA_ID = "HORMUZ_STRAIT"
"""The unseen-fleet snapshot covers the Hormuz region, and only that watched box."""

RADAR_SEVERITY = 0.9
"""A radar fleet that is half invisible is strong evidence, short of a confirmed finding."""

RADAR_MAX_CELLS = 200
"""Unmatched-detection cells carried in the alert's evidence, to keep the file small."""

GFW_ATTRIBUTION = "Powered by Global Fishing Watch"
"""The credit the contract requires on every view that shows radar detections."""

TRAIL_BEFORE_HOURS = 6
"""A trail starts this long before an alert's ``t_start`` and ends at the alert itself."""

TRAIL_AFTER_HOURS = 2
"""For an ended alert, the reappearance points up to this long after ``t_end``."""

TRAIL_MAX_POINTS = 60
"""Hard cap on a trail, so a fast hull with minute reports cannot bloat watch.json."""

REFRESH_SECONDS = 300
"""The loop interval the UI advertises; also written to the snapshot as ``refresh_s``."""

OUTAGE_MIN_MINUTES = 3.0
"""A receiver quiet run longer than this is an outage the UI should show as a gap."""

OUTAGE_MAX_MSGS_PER_MIN = 0
"""A minute at or below this many messages counts as the receiver being down.

Absolute rather than a fraction of the region's own median: a sparsely subscribed box (the
Laconian Gulf) legitimately hears a handful of messages a minute, so a relative floor would
report it as permanently out and lie about the recording.
"""

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

STOP_MIN_MINUTES = 30.0
"""A ``sanctioned_stop`` must last at least this long; a brief stop is manoeuvring or boarding."""

STOP_WINDOW_GAP_MIN = 30.0
"""Stopped pings closer together than this belong to the same stop window.

Anchored hulls report every few minutes, and one missed report must not split a long stop into
two short ones. Wider gaps (a hull that got underway and anchored again) start a new window.
"""

MAX_CONTACTS = 20
"""The contract's contact cap; requested higher from ``possible_contacts`` so a watchlisted
contact further out is not dropped by the distance cut before the watchlist-first sort."""

OFFSHORE_KM = MIN_OFFSHORE_KM
"""Ports, quays and inshore anchorages are not rendezvous: meetings, contacts and stops all
use the same five-kilometre gate as ``build_live``'s meeting detector."""


@dataclass
class _Tally:
    """Candidate and removal counts for one alert kind, for ``--explain`` only."""

    candidates: int = 0
    kept: int = 0
    removed: dict[str, int] = field(default_factory=dict)

    def drop(self, rule: str, n: int = 1) -> None:
        """Record that `n` candidates were removed by `rule`."""
        self.removed[rule] = self.removed.get(rule, 0) + n


@dataclass
class Explanation:
    """Per-kind rule counts for the ``--explain`` log; never written to the snapshot."""

    tallies: dict[str, _Tally] = field(default_factory=dict)

    def tally(self, kind: str) -> _Tally:
        """The tally for `kind`, created on first use."""
        return self.tallies.setdefault(kind, _Tally())

    def report(self) -> list[str]:
        """One line per kind: candidates, kept, and how many each rule removed."""
        lines: list[str] = []
        for kind in sorted(self.tallies):
            tally = self.tallies[kind]
            removed = ", ".join(f"{rule} {n}" for rule, n in tally.removed.items()) or "nothing"
            lines.append(
                f"explain {kind}: {tally.candidates} candidates, kept {tally.kept}, removed {removed}"
            )
        return lines


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


def _last_complete_minute(now: datetime) -> datetime:
    """The minute before the recording's newest one, the last minute the recorder finished.

    Why: ``now`` is the newest ping's minute, and that minute is still filling while the build
    reads it. ``kinematics.covered`` needs 150 messages in every minute of a gap, so a half-filled
    newest minute (66 messages against about 460 for a full minute) failed every open gap and
    dropped every ongoing dark alert. The build then flipped between 17 and 10 alerts depending
    on when in the minute it ran (issue #249). Judging coverage only to the last full minute
    makes the result a function of the recording, not of the build's wall-clock phase.
    """
    return now - timedelta(minutes=1)


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
        (event, "dark", "ongoing")
        for event in open_silences(tracks, rows, _last_complete_minute(now), activity=activity)
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
        min_offshore_km=OFFSHORE_KM,
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


def _decimate(points: Sequence[Ping], limit: int = TRAIL_MAX_POINTS) -> list[Ping]:
    """Keep at most ``limit`` points, always including both ends, evenly spaced in between.

    Selecting by index rather than by distance keeps the work O(n) after the window has been
    taken and makes the result independent of how the track list happens to be ordered. Both
    endpoints survive, which matters: the first point is where the hull came from and the last
    is where it was last seen before (or just after) the alert.
    """
    if len(points) <= limit:
        return list(points)
    last = len(points) - 1
    picks = sorted({round(index * last / (limit - 1)) for index in range(limit)})
    return [points[index] for index in picks]


def _trail(track: Sequence[Ping], start: datetime, end: datetime | None) -> list[list[Any]]:
    """The ship's recent path as ``[[lon, lat, "t"], ...]``, in time order.

    Six hours before the alert's ``t_start`` say where the hull came from; for an ended alert,
    up to two hours after ``t_end`` say where it reappeared. Five decimal places are about a
    metre, far finer than AIS itself, and keep the compact rows short.
    """
    if not track:
        return []
    begin = start - timedelta(hours=TRAIL_BEFORE_HOURS)
    points = [ping for ping in track if begin <= ping.t <= start]
    if end is not None:
        after_end = end + timedelta(hours=TRAIL_AFTER_HOURS)
        points.extend(ping for ping in track if end < ping.t <= after_end)
    points.sort(key=lambda ping: ping.t)
    return [[round(ping.lon, 5), round(ping.lat, 5), _iso(ping.t)] for ping in _decimate(points)]


def _dumps(value: Any, level: int = 0) -> str:
    """Pretty-print like ``json.dumps(indent=2)``, keeping scalar-only arrays on one line.

    ``indent=2`` puts every number of every trail point and radar cell on its own line, which
    triples the cost of the two new fields and blows the 40 % size budget. An array made only
    of numbers and strings (a bbox, a trail point, a density cell) has no structure worth
    indenting, so it stays inline; dicts and nested arrays keep the usual two-space layout.
    Values and key order are exactly those of ``json.dumps``, so readers see the same JSON.
    """
    pad = "  " * level
    inner = pad + "  "
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = [f"{inner}{json.dumps(key)}: {_dumps(item, level + 1)}" for key, item in value.items()]
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if all(not isinstance(item, (dict, list, tuple)) for item in value):
            return "[" + ", ".join(json.dumps(item) for item in value) + "]"
        rows = [f"{inner}{_dumps(item, level + 1)}" for item in value]
        return "[\n" + ",\n".join(rows) + "\n" + pad + "]"
    return json.dumps(value)


def _write_watch_json(path: Path, payload: dict[str, Any]) -> None:
    """Write the snapshot atomically, like ``build_live._write_json``, but with compact rows.

    The sibling writer indents nested numbers for every other snapshot; watch.json alone carries
    thousands of trail and cell coordinates, and the compact rows keep the file light.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A temp name per process: two writers must never share one half-written file (issue #249).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(_dumps(payload) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _listening_box(lat: float, lon: float) -> list[float] | None:
    """The first listening box containing `(lat, lon)`, or None when no box covers the fix.

    The same selection :func:`core.watch.open_silences` makes with its default ``areas``, so the
    ended and ongoing dark paths cannot disagree about which receiver footprint a last fix is
    leaving. None of the current boxes straddle the antimeridian, so a plain comparison is exact.
    """
    for area in LISTENING:
        min_lon, min_lat, max_lon, max_lat = area["bbox"]
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            return area["bbox"]
    return None


def _leaving_coverage(event: Event, tracks: dict[int, list[Ping]]) -> bool:
    """Whether this dark event's last fix was already driving out of our receivers.

    ``kinematics.silences`` events have no notion of our footprint - only
    ``open_silences`` applies :func:`core.watch.left_coverage` - so an ended gap that ends at
    the box edge would otherwise be reported as a switch-off. A missing fix cannot prove the
    ship was leaving, so it never suppresses the alert.
    """
    last = _last_ping(tracks.get(event.mmsi, []), event.start)
    if last is None:
        return False
    box = _listening_box(last.lat, last.lon)
    return box is not None and left_coverage(last, box)


def _watchlisted_could_meet(
    event: Event,
    *,
    tracks: dict[int, list[Ping]],
    categories: dict[int, str],
    classes: dict[int, str],
    land: Any,
    now: datetime,
) -> bool:
    """Whether some watchlisted hull could actually meet this dark ship.

    This must agree with the contract's meaning of "involving a watchlisted ship": a
    watchlisted *contact with* ``could_meet`` true. The 12 h recording showed why the old
    geometry-only test was wrong - a long gap's reach disk almost always contains a
    watchlisted hull, so ordinary traffic inherited a shadow-fleet label. Each watchlisted
    hull is represented by the same ping ``possible_contacts`` would show (closest to the dark
    ship's last fix), and that ping must pass :func:`contact_could_meet`: stopped, offshore
    and outside a routine anchorage. Scanning only watchlisted hulls (usually a few dozen)
    keeps the cheap pre-check for dark gaps in open water.
    """
    if not categories:
        return False
    # The same cap ``_alert_payload`` draws the reach with: a fast hull declared slow must not
    # look unreachable to this pre-check and lose the alert its contacts would have earned.
    cap_kn = effective_cap_kn(max_speed_knots(classes.get(event.mmsi, "default")), tracks.get(event.mmsi, []))
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
    closest: dict[int, tuple[float, datetime, Ping]] = {}
    for mmsi in categories:
        if mmsi == event.mmsi:
            continue
        for other in tracks.get(mmsi, []):
            if other.t < event.start or other.t > t_end:
                continue
            if not in_reach(last, next_fix, cap_kn, other.lat, other.lon, other.t):
                continue
            dist = distance_km(last.lat, last.lon, other.lat, other.lon)
            current = closest.get(mmsi)
            if current is None or (dist, other.t) < (current[0], current[1]):
                closest[mmsi] = (dist, other.t, other)
    return any(contact_could_meet(item[2], land) for item in closest.values())


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
    land: Any,
) -> list[dict[str, Any]]:
    """Join ``possible_contacts`` with the static identity and watchlist, sanctioned-first.

    ``possible_contacts`` already caps its own list; asking for more than the contract's 20
    lets the watchlist reorder happen before the cut, so a sanctioned hull that sits a little
    further out is still shown. ``land`` drives the offshore gate inside ``could_meet``.
    Identities that cannot be real MMSIs are dropped here too: an artefact is not a ship the
    screen may name as a possible meeting.
    """
    contacts = possible_contacts(
        dark_mmsi,
        last,
        next_fix,
        cap_kn,
        t_start,
        t_end,
        {mmsi: track for mmsi, track in tracks.items() if valid_mmsi(mmsi)},
        max_contacts=MAX_CONTACTS * 5,
        land=land,
        min_offshore_km=OFFSHORE_KM,
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
    """A short label for the watch screen: a few plain words, never a sentence."""
    if kind == "dark":
        # Only a contact that could actually meet earns the "within reach" wording: a
        # watchlisted hull merely inside the reach disk is not evidence of a meeting.
        meet = next(
            (contact for contact in contacts if contact["watchlist"] and contact["could_meet"]),
            None,
        )
        if meet is not None:
            category = str(meet["watchlist"]).replace("_", " ")
            km = round(float(meet["min_dist_km"]))
            return f"Switched off within reach of a {category} ship ({km} km)"
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
    land: Any,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    """One contract-shaped alert plus whether a watchlisted hull is involved somewhere in it.

    The involvement flag is returned rather than stored in the JSON: the contract has no such
    field, and the UI reads the same fact from ``contacts[].watchlist`` / the ship row.
    """
    mmsi = event.mmsi
    track = tracks.get(mmsi, [])
    class_cap_kn = max_speed_knots(classes.get(mmsi, "default"))
    # A dark hull's reach must match what its own track proves it can do, not only the class
    # it filed: a fast ship declaring a slow class otherwise gets a reach circle too small to
    # hold the contacts that were genuinely inside it. Other kinds keep the class cap.
    cap_kn = round(effective_cap_kn(class_cap_kn, track) if kind == "dark" else class_cap_kn, 3)
    contacts: list[dict[str, Any]] = []
    next_fix: Fix | None = None

    last_fix = _last_ping(track, event.start)
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
            land,
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
    # A watchlisted hull counts for a dark alert only when it could actually meet (the
    # contract's involvement rule; see _label).
    if any(contact["watchlist"] and contact["could_meet"] for contact in contacts):
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
        "trail": _trail(tracks.get(mmsi, []), event.start, event.end),
        "evidence": event.evidence,
    }
    return payload, involved


def _stop_location_ok(ping: Ping, land: Any) -> bool:
    """Whether a stop happened somewhere a sanctioned hull has no ordinary reason to wait.

    Either far enough from land (unknown land cannot exclude it) or inside a *watched* area.
    A listening box is only a feed subscription, so it is deliberately not enough; routine
    anchorage boxes are applied separately, before this test.
    """
    if area_of(ping.lat, ping.lon) in WATCHED_IDS:
        return True
    away = offshore_km(ping.lat, ping.lon, land)
    return away is None or away >= OFFSHORE_KM


def _stop_windows(pings: Sequence[Ping]) -> list[list[Ping]]:
    """Group stopped pings into stop windows, splitting where the hull got underway again.

    Pings closer together than :data:`STOP_WINDOW_GAP_MIN` belong to one window; a longer gap
    means a separate stop, so its duration is measured on its own rather than stretched across
    a period the hull was moving.
    """
    windows: list[list[Ping]] = []
    for ping in sorted(pings, key=lambda item: item.t):
        if windows and ping.t - windows[-1][-1].t <= timedelta(minutes=STOP_WINDOW_GAP_MIN):
            windows[-1].append(ping)
        else:
            windows.append([ping])
    return windows


def _sanctioned_stops(
    scan: _Scan,
    now: datetime,
    categories: dict[int, str],
    land: Any,
    *,
    explain: Explanation | None = None,
) -> list[dict[str, Any]]:
    """Watchlisted hulls stopped in open water or a watched area, long enough to matter.

    A sanctioned tanker waiting at Fujairah or Singapore is ordinary port business; a short
    stop is manoeuvring, pilot boarding or waiting for a berth. So an alert needs all three:
    outside every routine anchorage, at least five km offshore or inside a watched area (never
    a listening box alone), and a stop window of at least 30 minutes. Repeats merge into one
    alert per hull per build window - the screen wants one story per ship, not one per ping.
    """
    tally = explain.tally("sanctioned_stop") if explain is not None else None
    payloads: list[dict[str, Any]] = []
    for mmsi, category in sorted(categories.items()):
        track = scan.tracks.get(mmsi)
        if not track:
            continue
        if not valid_mmsi(mmsi):
            if tally is not None:
                tally.drop("invalid MMSI")
            continue
        stopped = [ping for ping in track if ping.sog is not None and ping.sog <= STOP_SOG_KN]
        if not stopped:
            continue
        if tally is not None:
            tally.candidates += 1
        located = [
            ping
            for ping in stopped
            if context_for(ping.lat, ping.lon) is None and _stop_location_ok(ping, land)
        ]
        if not located:
            if tally is not None:
                tally.drop("anchorage/inshore")
            continue
        windows = [
            window
            for window in _stop_windows(located)
            if window[-1].t - window[0].t >= timedelta(minutes=STOP_MIN_MINUTES)
        ]
        if not windows:
            if tally is not None:
                tally.drop("under 30 min")
            continue
        if tally is not None and len(windows) > 1:
            tally.drop("merged repeat stop", len(windows) - 1)
        window = windows[-1]
        first, ping = window[0], window[-1]
        latest = max(track, key=lambda item: item.t)
        ongoing = ping.t >= latest.t and (now - ping.t) <= timedelta(minutes=STOP_MIN_MINUTES)
        cls = scan.classes.get(mmsi) or "default"
        area_id = area_of(ping.lat, ping.lon)
        severity = _raise_severity(
            SANCTIONED_STOP_SEVERITY,
            watched=area_id in WATCHED_IDS,
            involved=category in SUSPICIOUS_CATEGORIES,
        )
        label = f"Sanctioned {cls} stopped" if cls != "default" else "Sanctioned ship stopped"
        if tally is not None:
            tally.kept += 1
        payloads.append(
            {
                "id": f"sanctioned_stop-{mmsi}-{int(first.t.timestamp())}",
                "kind": "sanctioned_stop",
                "status": "ongoing" if ongoing else "ended",
                "mmsi": mmsi,
                "name": scan.region.vessels[mmsi].name if mmsi in scan.region.vessels else None,
                "area_id": area_id,
                "severity": severity,
                "label": label,
                "t_start": _iso(first.t),
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
                "trail": _trail(track, first.t, None if ongoing else ping.t),
                "evidence": {
                    "sog": ping.sog,
                    "class": cls,
                    "anchorage": None,
                    "minutes": round((ping.t - first.t).total_seconds() / 60.0, 1),
                },
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


def _strait_centroid() -> tuple[float, float]:
    """The centre of the Hormuz watched box: the radar alert has no per-vessel position."""
    for area in WATCHED:
        if area["id"] == RADAR_AREA_ID:
            min_lon, min_lat, max_lon, max_lat = area["bbox"]
            return ((min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0)
    raise KeyError(f"{RADAR_AREA_ID} missing from core.areas.WATCHED")


def _unseen_path(unseen: Path | None) -> Path | None:
    """Use the given unseen snapshot, falling back to the repo-relative default.

    ``--loop`` and direct ``build()`` calls may run from any working directory, so a relative
    default is resolved against the repository root rather than the caller's cwd.
    """
    if unseen is None:
        return None
    path = Path(unseen)
    if path.is_file():
        return path
    if not path.is_absolute():
        fallback = ROOT / path
        if fallback.is_file():
            return fallback
    return path


def _well_covered_days(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The daily rows that carried enough radar passes to be worth reading.

    ``build_unseen_fleet.py`` already made this call: low-coverage days sit in
    ``summary.excluded_days``. A file without a summary is treated as all-usable rather than
    silently producing no alert.
    """
    daily = [row for row in data.get("daily") or [] if isinstance(row, dict) and row.get("day") is not None]
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    excluded = {str(day) for day in summary.get("excluded_days") or []}
    used = [row for row in daily if str(row["day"]) not in excluded]
    return used or daily


def _radar_cells(grid: Any) -> list[list[float]]:
    """Up to :data:`RADAR_MAX_CELLS` unmatched-detection cells as ``[[lon, lat], ...]``.

    Densest cells come first: the map's job is to show where the radar saw ships that were not
    broadcasting, and a 200-cell cap keeps the evidence block from dominating watch.json. Ties
    break on coordinates so repeated builds are byte-stable.
    """
    ranked: list[tuple[int, float, float]] = []
    for cell in grid or []:
        if not isinstance(cell, dict):
            continue
        lon, lat = cell.get("lon"), cell.get("lat")
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        try:
            count = int(cell.get("n") or 0)
        except (TypeError, ValueError):
            count = 0
        ranked.append((count, float(lon), float(lat)))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [[round(lon, 4), round(lat, 4)] for _, lon, lat in ranked[:RADAR_MAX_CELLS]]


def _radar_alert(unseen: Path | None) -> dict[str, Any] | None:
    """One fleet-level alert for the latest well-covered radar day, or None when unavailable.

    The unseen snapshot is daily and fleet-wide: it can say how many hulls radar saw without an
    AIS broadcast, never which ones. So the alert names no ship (``mmsi`` 0, ``name`` null),
    spans that UTC day, and carries the counts and cells as evidence. A missing file is not an
    error - the watch screen simply has no radar layer for this build.
    """
    path = _unseen_path(unseen)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    used = _well_covered_days(data)
    if not used:
        return None
    row = max(used, key=lambda item: str(item["day"]))
    try:
        day = date.fromisoformat(str(row["day"]))
    except ValueError:
        return None

    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    try:
        seen = int(row.get("seen") or 0)
    except (TypeError, ValueError):
        seen = 0
    raw_unmatched = row.get("unmatched")
    if raw_unmatched is None:
        raw_unmatched = row.get("dark")
    try:
        unmatched = int(raw_unmatched or 0)
    except (TypeError, ValueError):
        unmatched = 0

    median = summary.get("median_dark_share")
    if not isinstance(median, (int, float)):
        shares = [
            float(item["dark_share"]) for item in used if isinstance(item.get("dark_share"), (int, float))
        ]
        median = statistics.median(shares) if shares else 0.0
    days_used = summary.get("n_days_used")
    if not isinstance(days_used, int):
        days_used = len(used)

    start = datetime.combine(day, dtime.min, tzinfo=UTC)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    lat, lon = _strait_centroid()
    return {
        "id": f"radar_unmatched-0-{int(start.timestamp())}",
        "kind": "radar_unmatched",
        "status": "ended",
        "mmsi": 0,
        "name": None,
        "area_id": RADAR_AREA_ID,
        "severity": RADAR_SEVERITY,
        "label": f"Radar: {unmatched} vessels not broadcasting",
        "t_start": _iso(start),
        "t_end": _iso(end),
        "last": {"lat": lat, "lon": lon, "t": _iso(end), "sog": None, "cog": None},
        "next": None,
        "speed_cap_kn": 0.0,
        "contacts": [],
        "trail": [],
        "evidence": {
            "day": day.isoformat(),
            "seen": seen,
            "unmatched": unmatched,
            "share": round(unmatched / seen, 4) if seen else 0.0,
            "median_share_well_covered_days": round(float(median), 4),
            "days_used": days_used,
            "cells": _radar_cells(data.get("grid")),
            "source": GFW_ATTRIBUTION,
        },
    }


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


def _gap_row(region: str, start: datetime, end: datetime) -> dict[str, str] | None:
    """One outage row for a quiet run of minutes, or None when the run is short enough to ignore.

    Both ends are inclusive minute labels, matching ``build_live._coverage_summary``'s holes, so
    the UI can draw the same interval from either snapshot. A run of N minutes spans N - 1
    minutes between its labels, hence the +1 when comparing with :data:`OUTAGE_MIN_MINUTES`.
    """
    minutes = (end - start).total_seconds() / 60.0 + 1.0
    if minutes <= OUTAGE_MIN_MINUTES:
        return None
    return {"region": region, "start": _iso(start), "end": _iso(end)}


def _receiver_gaps(region: str, rows: Sequence[CoverageMinute]) -> list[dict[str, str]]:
    """Receiver outages longer than :data:`OUTAGE_MIN_MINUTES` inside one region's window.

    The window is the span the region reported anything at all: leading and trailing quiet time
    has no coverage to be missing from, so it is not claimed as an outage (the snapshot's
    ``lag_s`` is what tells the UI the recording itself is stale). A minute with no row, or one
    at or below :data:`OUTAGE_MAX_MSGS_PER_MIN`, is down; adjacent down minutes merge into one
    gap so a ten-minute hole is one row, not ten.
    """
    counts: dict[datetime, int] = {}
    for row in rows:
        minute = row.minute.replace(second=0, microsecond=0)
        counts[minute] = counts.get(minute, 0) + row.n_msgs
    if not counts:
        return []
    gaps: list[dict[str, str]] = []
    run_start: datetime | None = None
    minute = min(counts)
    last = max(counts)
    while minute <= last:
        down = counts.get(minute, 0) <= OUTAGE_MAX_MSGS_PER_MIN
        if down and run_start is None:
            run_start = minute
        elif not down and run_start is not None:
            row = _gap_row(region, run_start, minute - timedelta(minutes=1))
            if row is not None:
                gaps.append(row)
            run_start = None
        minute += timedelta(minutes=1)
    if run_start is not None:
        row = _gap_row(region, run_start, last)
        if row is not None:
            gaps.append(row)
    return gaps


def _recording(regions: Sequence[_Region], scans: Sequence[_Scan], stamp: datetime) -> dict[str, Any]:
    """The freshness block: how old the newest fix is, and where the receivers themselves were out.

    ``lag_s`` is measured against the build's own clock (`stamp`, the same instant as
    ``generated_at``), not the recording-derived ``now``: the UI's "last update N min ago" must
    keep growing while the recorder is stopped, which a clock read back out of the recording can
    never show. It is clamped at zero so a recording dated in the future reads as "now" rather
    than as a negative age.
    """
    latest = max((max(ping.t for ping in region.pings) for region in regions if region.pings), default=None)
    lag_s = None if latest is None else max(0, int(round((stamp - latest).total_seconds())))
    gaps = [gap for scan in scans for gap in _receiver_gaps(scan.region.name, scan.rows)]
    return {"latest_t": _iso(latest) if latest is not None else None, "lag_s": lag_s, "gaps": gaps}


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
                # Kept in the list, but flagged: an identity that cannot be a real MMSI is a
                # data artefact, so the map may draw it while the alert pipeline never names it.
                "valid": valid_mmsi(mmsi),
                "name": info.name if info else None,
                "lat": last.lat,
                "lon": last.lon,
                "t": _iso(last.t),
                "sog": last.sog,
                # Last known course in degrees, null when the hull sent none. The map turns a
                # chevron by it; a stopped or course-less hull is drawn as a dot.
                "cog": last.cog,
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
    unseen: Path | None = DEFAULT_UNSEEN,
    explain: Explanation | None = None,
) -> dict[str, Any]:
    """Read every region, write ``watch.json``, and return the payload.

    ``explain`` is an optional collector: when supplied it records, per alert kind, how many
    candidates each rule removed for ``--explain``. It never changes what is written.
    """
    regions = _load_regions(Path(ais_dir), hours)
    latest = max((max(ping.t for ping in region.pings) for region in regions if region.pings), default=None)
    now = (latest or datetime.now(UTC)).replace(second=0, microsecond=0)
    # The build's own clock: shared by generated_at and the freshness lag, so the two can never
    # disagree about when this snapshot was made.
    stamp = datetime.now(UTC).replace(microsecond=0)

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
    # Only an exact IMO hit labels a ship. imo_watchlist also returns name-only matches
    # (strong_name/weak_name); on live data that labelled innocent ferries (AURORA, ROMINA) as
    # sanctioned because their names appear on the list for other hulls. A name is not an
    # identity (docs/insights.md #10), so name hits never reach the UI or the alert rules.
    categories = {
        mmsi: str(case["category"])
        for mmsi, case in cases.items()
        if case.get("category") in SUSPICIOUS_CATEGORIES and case.get("strength") == "imo"
    }
    watch_rows = [{"mmsi": mmsi, "category": category} for mmsi, category in categories.items()]

    land_geom = load_land(land) if land is not None and Path(land).is_file() else None
    scans = [_scan(region, now, land_geom, watch_rows) for region in regions]
    all_tracks = kinematics.split_tracks([ping for region in regions for ping in region.pings])

    alerts: list[dict[str, Any]] = []
    alerted: set[int] = set()
    seen: set[str] = set()
    stopped_ships: set[int] = set()
    hidden = 0

    for scan in scans:
        for event, kind, status in scan.candidates:
            tally = explain.tally(kind) if explain is not None else None
            if tally is not None:
                tally.candidates += 1
            # No alert of any kind for an identity that cannot be a real MMSI: it is a decode
            # artefact, and naming it would invent a ship the screen would ask the crew about.
            if not valid_mmsi(event.mmsi):
                hidden += 1
                if tally is not None:
                    tally.drop("invalid MMSI")
                continue
            if kind == "dark":
                # An ended gap has no footprint test of its own (only open_silences has), so a
                # hull that crossed the box edge westbound would be reported as a switch-off.
                if _leaving_coverage(event, scan.tracks):
                    hidden += 1
                    if tally is not None:
                        tally.drop("left coverage")
                    continue
                info = scan.region.vessels.get(event.mmsi)
                if not dark_type_allowed(info.ship_type if info is not None else None):
                    hidden += 1
                    if tally is not None:
                        tally.drop("type")
                    continue
                own = categories.get(event.mmsi) is not None
                if not own and area_of(event.lat, event.lon) not in WATCHED_IDS:
                    if not _watchlisted_could_meet(
                        event,
                        tracks=all_tracks,
                        categories=categories,
                        classes=scan.classes,
                        land=land_geom,
                        now=now,
                    ):
                        hidden += 1
                        if tally is not None:
                            tally.drop("no watchlisted meeting")
                        continue
            payload, involved = _alert_payload(
                event,
                kind,
                status,
                vessels=vessels,
                categories=categories,
                classes=scan.classes,
                tracks=all_tracks,
                land=land_geom,
                now=now,
            )
            if not _is_suspicious(payload, involved):
                hidden += 1
                if tally is not None:
                    tally.drop("not suspicious")
                continue
            if payload["id"] in seen:
                if tally is not None:
                    tally.drop("duplicate")
                continue
            seen.add(payload["id"])
            alerts.append(payload)
            if tally is not None:
                tally.kept += 1
            alerted.add(payload["mmsi"])
            alerted.update(contact["mmsi"] for contact in payload["contacts"])
        for payload in _sanctioned_stops(scan, now, categories, land_geom, explain=explain):
            if payload["mmsi"] in stopped_ships or payload["id"] in seen:
                if explain is not None:
                    explain.tally("sanctioned_stop").drop("repeat across regions")
                continue
            stopped_ships.add(payload["mmsi"])
            seen.add(payload["id"])
            alerts.append(payload)
            alerted.add(payload["mmsi"])

    radar = _radar_alert(unseen)
    if radar is not None:
        alerts.append(radar)

    alerts.sort(key=lambda alert: (-alert["severity"], alert["id"]))
    alert_ids: dict[int, list[str]] = {}
    for alert in alerts:
        for mmsi in {alert["mmsi"]} | {contact["mmsi"] for contact in alert["contacts"]}:
            alert_ids.setdefault(mmsi, [])
            if alert["id"] not in alert_ids[mmsi]:
                alert_ids[mmsi].append(alert["id"])
    for mmsi in alert_ids:
        alert_ids[mmsi].sort()

    hidden += sum(
        _routine_quiet(scan.tracks, scan.rows, _last_complete_minute(now), alerted)
        for scan in scans
        if scan.tracks
    )

    ships = _ships(all_tracks, vessels, categories, alert_ids, now)
    payload = {
        # The build's own clock, not the recording's: the SSE client compares this value to
        # decide a new file is worth refetching, so it must change on every run.
        "generated_at": _iso(stamp),
        "refresh_s": REFRESH_SECONDS,
        # How live the recording is, and where the receivers themselves were out: the UI says
        # "last update N min ago" from this instead of trusting the snapshot's own age.
        "recording": _recording(regions, scans, stamp),
        "areas": [dict(area) for area in [*LISTENING, *WATCHED]],
        "ships": ships,
        "alerts": alerts,
        "counts": {"ships": len(ships), "alerts": len(alerts), "hidden_routine": hidden},
    }
    _write_watch_json(Path(out) / "watch.json", payload)
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
    unseen: Path | None = DEFAULT_UNSEEN,
    explain: bool = False,
) -> dict[str, Any]:
    """One build plus the single log line ``--loop`` prints per iteration."""
    started = time.perf_counter()
    explanation = Explanation() if explain else None
    payload = build(
        hours=hours,
        ais_dir=ais_dir,
        out=out,
        sanctions=sanctions,
        land=land,
        unseen=unseen,
        explain=explanation,
    )
    elapsed = time.perf_counter() - started
    counts = payload["counts"]
    print(
        f"watch {_generated_at(payload)}: {counts['alerts']} alerts, "
        f"{counts['hidden_routine']} hidden routine, {counts['ships']} ships in {elapsed:.1f}s",
        flush=True,
    )
    if explanation is not None:
        for line in explanation.report():
            print(line, flush=True)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the global watch snapshot from recordings.")
    parser.add_argument("--hours", type=int, default=12, help="newest N hours of each recording")
    parser.add_argument("--loop", type=int, default=0, help="re-run every N seconds, forever")
    parser.add_argument("--ais", type=Path, default=DEFAULT_AIS, help="recordings root")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot output folder")
    parser.add_argument("--land", type=Path, default=DEFAULT_LAND, help="land GeoJSON")
    parser.add_argument("--sanctions", type=Path, default=DEFAULT_SANCTIONS, help="sanctions CSV")
    parser.add_argument(
        "--unseen",
        type=Path,
        default=DEFAULT_UNSEEN,
        help="radar-vs-AIS snapshot (hormuz_unseen.json); a missing file adds no radar alert",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="print, per alert kind, how many candidates each rule removed",
    )
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
        "unseen": args.unseen,
        "explain": args.explain,
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
