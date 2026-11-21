"""Build a region-agnostic live snapshot from a recorded AIS folder.

    uv run python scripts/build_live.py --region BALTIC_WATCH [--hours 12]
        [--out data/snapshot] [--land web/public/basemap/land.geojson]
        [--sanctions data/sanctions/maritime.csv]
        [--meeting-types cargo-tanker|all] [--min-offshore-km 5]

The recorder drops one ``*.jsonl.gz`` per UTC hour under ``data/cache/ais/<REGION>/``, but the
engine itself knows nothing about a region: it takes whatever pings it is given. This script is
therefore the single bridge from a recording to the read-only ``live_<REGION>.json`` shape the
API and the live screen consume, for any folder the recorder produced.

It parses once through :func:`darkfleet.ingest.ais_tracks.iter_files`, then hands the pings to
the same pure detectors the Baltic snapshot uses. Two deliberate join decisions live here rather
than in ``core/``:

* speed caps come from the static ship type (``tracks.ship_class`` -> ``core.speeds``), because a
  jump is only impossible relative to the class of the hull that reported it;
* jumps reported by an MMSI that also shows a clone are dropped, because two transmitters sharing
  one identity explain every impossible hop; reporting both would double-count one cause.

Planning runs before issue #95 may have added an activity index to ``core.kinematics``, so the
optional ``activity=`` argument is passed only when that module exposes the builder.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from darkfleet.core import kinematics
from darkfleet.core.anchorages import context_for
from darkfleet.core.encounters import meetings
from darkfleet.core.geo import distance_km
from darkfleet.core.speeds import max_speed_knots
from darkfleet.core.spoofing import filter_explained_jumps, ghost_points
from darkfleet.core.tracks import (
    CoverageMinute,
    Event,
    Ping,
    VesselInfo,
    ship_class,
)
from darkfleet.core.water import load_land
from darkfleet.ingest.ais_tracks import coverage, iter_files
from darkfleet.ingest.watchlist import imo_watchlist

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AIS = Path("data/cache/ais")
DEFAULT_OUT = Path("data/snapshot")
DEFAULT_LAND = Path("web/public/basemap/land.geojson")
DEFAULT_SANCTIONS = Path("data/sanctions/maritime.csv")

HOLE_FRACTION = 0.2
"""A minute is a coverage hole when it holds less than this share of the median minute."""

MAX_EVENTS = 500
"""The live screen cannot draw more than this many markers usefully; the head is the answer."""

KEEP_STRENGTHS = frozenset({"imo", "strong_name"})
"""Watchlist strengths worth surfacing: a hull number or a distinctive full name, not a guess."""

MEETING_TYPES = ("cargo-tanker", "all")
"""CLI choices for who can be in a meeting: the two commercial classes, or every hull."""

CARGO_TANKER_TYPES = frozenset(range(70, 90))
"""AIS ship-type codes 70-89: cargo (70-79) and tanker (80-89), the oil-transfer classes.

Fishing, passenger and unknown hulls are excluded by default because the Baltic recordings
showed most of their close pairs were harbour traffic rather than rendezvous. Unknown is
never treated as a member: an untyped hull cannot prove it is a cargo ship or a tanker.
"""

ALERT_CATEGORIES = frozenset({"sanctioned", "shadow_fleet"})
"""Watchlist categories that are an alert; detention is context, not an accusation."""


def _drop_spoofed_clones(clones: list[Event], spoofs: list[Event], km: float = 2.0) -> list[Event]:
    """A 'clone' whose second position is a ghost point is a spoofing victim, not two transmitters.

    Found on real data: MMSIs flip-flopping between their real track and the St Petersburg ghost
    point looked like cloned identities; the spoof event already names them, so drop the clone.
    """
    centres = [(s.lat, s.lon) for s in spoofs]
    kept = []
    for event in clones:
        ends = [event.evidence.get(k) for k in ("cluster_a", "cluster_b")]
        ends = [e for e in ends if isinstance(e, (list, tuple)) and len(e) == 2]
        near = any(distance_km(e[0], e[1], c[0], c[1]) <= km for e in ends for c in centres)
        if not near:
            kept.append(event)
    return kept


def resolve_region_dir(region: str) -> Path:
    """Find a region folder from a bare name or a path.

    ``--region BALTIC_WATCH`` means ``data/cache/ais/BALTIC_WATCH`` relative to the working
    directory (the CLI contract), while an absolute or relative folder path is accepted as-is so
    tests and one-off recordings can point at a temp directory. The repo-relative location is the
    last fallback, which keeps ``uv run`` from a script directory working.
    """
    candidate = Path(region).expanduser()
    if candidate.is_dir():
        return candidate
    local = DEFAULT_AIS / region
    if local.is_dir():
        return local
    return ROOT / DEFAULT_AIS / region


def _merge_info(previous: VesselInfo | None, current: VesselInfo) -> VesselInfo:
    """Keep the newest non-empty value of every static field.

    Message 5 and message 24 arrive in pieces (and class-B part B carries no name), so letting a
    later report blank an earlier field would lose a ship's name for no reason.
    """
    if previous is None:
        return current
    return VesselInfo(
        mmsi=current.mmsi,
        imo=current.imo if current.imo is not None else previous.imo,
        name=current.name if current.name is not None else previous.name,
        callsign=current.callsign if current.callsign is not None else previous.callsign,
        ship_type=current.ship_type if current.ship_type is not None else previous.ship_type,
        length_m=current.length_m if current.length_m is not None else previous.length_m,
        destination=current.destination if current.destination is not None else previous.destination,
        last_seen=current.last_seen if current.last_seen is not None else previous.last_seen,
    )


def _read_pings_and_vessels(directory: Path) -> tuple[list[Ping], dict[int, VesselInfo]]:
    """Parse a recording once, keeping position reports and the latest static record per MMSI."""
    pings: list[Ping] = []
    vessels: dict[int, VesselInfo] = {}
    for obj in iter_files([directory]):
        if isinstance(obj, Ping):
            pings.append(obj)
        else:
            vessels[obj.mmsi] = _merge_info(vessels.get(obj.mmsi), obj)
    return pings, vessels


def _last_hours(pings: list[Ping], hours: int | None) -> list[Ping]:
    """Keep only the newest `hours` of pings, measured back from the recording's last ping.

    The recording is the clock: a wall-clock cutoff would silently produce an empty snapshot
    when replaying an older capture. `hours is None` keeps everything.
    """
    if hours is None or not pings:
        return pings
    end = max(ping.t for ping in pings)
    cutoff = end - timedelta(hours=hours)
    return [ping for ping in pings if ping.t >= cutoff]


def _iso(t: datetime) -> str:
    """UTC ISO-8601 with a trailing Z, the spelling every snapshot field uses."""
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _coverage_summary(rows: Sequence[CoverageMinute]) -> dict[str, Any]:
    """Minute count, median traffic and merged holes for the UI's coverage strip.

    A hole is any minute below :data:`HOLE_FRACTION` of the median, because a receiver that is
    hearing a fifth of its usual traffic is effectively down for silence detection. Adjacent low
    minutes are one outage, so they are merged into a single ``[start, end]`` range; both ends are
    inclusive minute labels.
    """
    if not rows:
        return {"minutes": 0, "median_msgs_per_min": 0.0, "holes": []}
    values = [row.n_msgs for row in rows]
    median_msgs = float(median(values))
    threshold = median_msgs * HOLE_FRACTION
    holes: list[dict[str, str]] = []
    start: datetime | None = None
    end: datetime | None = None
    for row in rows:
        if row.n_msgs < threshold:
            if start is None:
                start = row.minute
            end = row.minute
        elif start is not None and end is not None:
            holes.append({"start": _iso(start), "end": _iso(end)})
            start = end = None
    if start is not None and end is not None:
        holes.append({"start": _iso(start), "end": _iso(end)})
    return {"minutes": len(rows), "median_msgs_per_min": median_msgs, "holes": holes}


def _run_silences(tracks: dict[int, list[Ping]], rows: Sequence[CoverageMinute]) -> list[Event]:
    """Silence events, using issue #95's activity index when the installed module has it.

    The index is optional by design so this builder works on either side of that merge; it only
    sharpens ``covered`` with per-receiver activity, while the public ``silences`` contract stays
    the same.
    """
    activity = None
    if hasattr(kinematics, "build_activity_index"):
        activity = kinematics.build_activity_index(tracks)
    events: list[Event] = []
    for track in tracks.values():
        if activity is None:
            events.extend(kinematics.silences(track, rows))
        else:
            events.extend(kinematics.silences(track, rows, activity=activity))
    return events


def _vessel_rows(tracks: dict[int, list[Ping]], vessels: dict[int, VesselInfo]) -> list[dict[str, Any]]:
    """One row per MMSI seen, carrying static identity plus its last reported position."""
    rows: list[dict[str, Any]] = []
    for mmsi in sorted(tracks):
        track = tracks[mmsi]
        if not track:
            continue
        info = vessels.get(mmsi)
        last = track[-1]
        rows.append(
            {
                "mmsi": mmsi,
                "name": info.name if info else None,
                "imo": info.imo if info else None,
                "ship_type": info.ship_type if info else None,
                "class": ship_class(info.ship_type if info else None),
                "last": {
                    "t": _iso(last.t),
                    "lat": last.lat,
                    "lon": last.lon,
                    "sog": last.sog,
                },
            }
        )
    return rows


def _watchlist_rows(
    tracks: dict[int, list[Ping]],
    vessels: dict[int, VesselInfo],
    sanctions: Path | None,
) -> list[dict[str, Any]]:
    """Sanctions hits for observed hulls, keeping only the two evidence strengths we trust.

    A missing CSV (it is CC BY-NC and never committed) simply yields an empty list, so the live
    snapshot still builds on a fresh checkout.
    """
    if sanctions is None or not Path(sanctions).is_file():
        return []
    seen = [vessels[mmsi] for mmsi in tracks if mmsi in vessels]
    matches = imo_watchlist(seen, Path(sanctions))
    kept = [case for case in matches.values() if case.get("strength") in KEEP_STRENGTHS]
    return sorted(kept, key=lambda case: int(case.get("mmsi") or 0))


def _split_watchlist(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split watchlist rows into alerts and informational hits.

    The live screen paints red only for a sanctions or shadow-fleet hit; a detention is
    worth showing but it is a port-state action, not evidence of a dark-fleet operator.
    Keeping the two lists separate lets the UI style them differently without re-reading
    categories, and an unknown category falls to ``info`` rather than disappearing.
    """
    alerts: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    for row in rows:
        (alerts if row.get("category") in ALERT_CATEGORIES else info).append(row)
    return {"alerts": alerts, "info": info}


def _meeting_context(meeting_events: Sequence[Event], alerts: Sequence[dict[str, Any]]) -> list[Event]:
    """Re-read each meeting with the water it happened in and the hulls that were in it.

    Two join decisions, both about a meeting's *context* rather than its geometry:

    * a meeting inside a known anchorage is probably routine refuelling, so its severity is
      halved and the anchorage is named in the evidence. The box is hand-drawn, which the note
      says out loud;
    * a meeting where either hull is a sanctions or shadow-fleet alert is worth a look whatever
      the water: severity is floored at 0.9 and the alert categories are attached. This is
      checked after the anchorage discount, so a sanctioned pair bunkering in Singapore still
      ranks high.

    ``Event`` is frozen, so each changed finding is rebuilt with :func:`dataclasses.replace`;
    a meeting that matches neither rule is passed through unchanged.
    """
    categories: dict[int, list[str]] = {}
    for row in alerts:
        mmsi = int(row.get("mmsi") or 0)
        category = row.get("category")
        if mmsi and category:
            categories.setdefault(mmsi, []).append(str(category))

    contextualised: list[Event] = []
    for event in meeting_events:
        severity = event.severity
        explanation = event.explanation
        evidence = dict(event.evidence)
        changed = False

        context = context_for(event.lat, event.lon)
        if context is not None:
            severity = severity / 2.0
            evidence["context"] = {
                "name": context["name"],
                "kind": context["kind"],
                "note": context["note"],
            }
            explanation += (
                f" In a known anchorage ({context['name']}): likely routine refuelling "
                f"(bunkering); transfers outside such areas deserve a closer look."
            )
            changed = True

        watchlist_categories: list[str] = []
        for mmsi in (event.mmsi, event.other_mmsi):
            if mmsi is None:
                continue
            for category in categories.get(mmsi, []):
                if category not in watchlist_categories:
                    watchlist_categories.append(category)
        if watchlist_categories:
            severity = max(severity, 0.9)
            evidence["watchlist"] = watchlist_categories
            explanation += " One vessel is on a sanctions list."
            changed = True

        if not changed:
            contextualised.append(event)
            continue
        contextualised.append(
            replace(
                event,
                severity=round(min(1.0, max(0.0, severity)), 3),
                explanation=explanation,
                evidence=evidence,
            )
        )
    return contextualised


def _event_payload(event: Event) -> dict[str, Any]:
    """Event as a JSON dict, with datetimes rendered as ISO strings."""
    row = asdict(event)
    row["start"] = _iso(event.start)
    row["end"] = _iso(event.end)
    return row


def _event_sort_key(row: dict[str, Any]) -> tuple[float, str, str, int]:
    """Strongest first, with stable tie-breakers so two runs produce the same file."""
    return (-float(row["severity"]), str(row["start"]), str(row["kind"]), int(row["mmsi"]))


def build(
    region: str,
    directory: Path,
    out: Path,
    *,
    hours: int | None = None,
    land: Path | None = None,
    sanctions: Path | None = None,
    meeting_types: str = "cargo-tanker",
    min_offshore_km: float = 5.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run every detector over one recording and write ``live_<region>.json``.

    Pure detectors are called with the whole cleaned track map where they are pairwise (meetings)
    and per track where they are not (silences, jumps, clones); this keeps the cost linear in
    pings for the per-vessel rules. Returns the payload for callers that want to assert on it.

    Meetings default to cargo/tanker hulls at least 5 km offshore. The Baltic recording's
    meeting false positives were moored or harbour-adjacent hulls, so the filter is applied at
    the join (static types plus a wider land buffer) rather than in ``core.encounters``; a
    caller that wants every hull passes ``meeting_types="all"``.
    """
    if meeting_types not in MEETING_TYPES:
        raise ValueError(f"meeting_types must be one of {MEETING_TYPES}, got {meeting_types!r}")
    pings, vessels = _read_pings_and_vessels(Path(directory))
    pings = _last_hours(pings, hours)

    rows = coverage(ping.t for ping in pings)
    tracks = kinematics.split_tracks(pings)
    classes = {mmsi: ship_class(vessels[mmsi].ship_type if mmsi in vessels else None) for mmsi in tracks}

    silences = _run_silences(tracks, rows)
    clone_events: list[Event] = []
    jump_events: list[Event] = []
    for mmsi, track in tracks.items():
        clone_events.extend(kinematics.clones(track))
        jump_events.extend(kinematics.jumps(track, max_speed_knots(classes[mmsi])))

    # A clone means two transmitters share the MMSI; the resulting teleports are not the ship
    # going somewhere impossible, so the clone event already tells the whole story.
    cloned_mmsis = {event.mmsi for event in clone_events}
    jump_events = [event for event in jump_events if event.mmsi not in cloned_mmsis]

    land_geom = load_land(land) if land is not None and Path(land).is_file() else None
    if meeting_types == "cargo-tanker":
        vessel_types = {mmsi: info.ship_type for mmsi, info in vessels.items()}
        meeting_events = meetings(
            tracks,
            land=land_geom,
            min_offshore_km=min_offshore_km,
            ship_types=CARGO_TANKER_TYPES,
            vessel_types=vessel_types,
        )
    else:
        meeting_events = meetings(tracks, land=land_geom, min_offshore_km=min_offshore_km)
    # Ghost points: several unrelated ships reporting one fixed (often inland) point is GNSS
    # spoofing; their "jumps" to that point are the spoof, not the ships, so drop those jumps.
    spoof_events = ghost_points(tracks, land=land_geom)
    jump_events = filter_explained_jumps(jump_events, spoof_events)
    clone_events = _drop_spoofed_clones(clone_events, spoof_events)

    watch = _watchlist_rows(tracks, vessels, sanctions)
    watchlist = _split_watchlist(watch)
    # Anchorage context and sanctions alerts are read after the detectors because both need
    # data the geometry alone does not have: where the meeting was, and who was in it.
    meeting_events = _meeting_context(meeting_events, watchlist["alerts"])

    events = [*silences, *jump_events, *clone_events, *meeting_events, *spoof_events]
    payload_events = sorted((_event_payload(event) for event in events), key=_event_sort_key)
    payload_events = payload_events[:MAX_EVENTS]

    by_kind = Counter(event.kind for event in events)
    generated_at = now or datetime.now(UTC)
    start = min((ping.t for ping in pings), default=None)
    end = max((ping.t for ping in pings), default=None)
    payload = {
        "region": region,
        "generated_at": _iso(generated_at),
        "window": {
            "start": _iso(start) if start else None,
            "end": _iso(end) if end else None,
        },
        "coverage": _coverage_summary(rows),
        "counts": {
            "pings": len(pings),
            "vessels": len(tracks),
            "silences": by_kind.get("silence", 0),
            "jumps": by_kind.get("jump", 0),
            "clones": by_kind.get("clone", 0),
            "meetings": by_kind.get("meeting", 0),
            "spoofs": by_kind.get("spoof", 0),
            "watchlist": len(watch),
            "watchlist_alerts": len(watchlist["alerts"]),
            "watchlist_info": len(watchlist["info"]),
        },
        "events": payload_events,
        "watchlist": watchlist,
        "vessels": _vessel_rows(tracks, vessels),
    }

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    name = Path(region).name or "region"
    (out / f"live_{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a live AIS snapshot for one region.")
    parser.add_argument("--region", required=True, help="folder name under data/cache/ais")
    parser.add_argument("--hours", type=int, default=None, help="only the newest N hours (default all)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot output folder")
    parser.add_argument("--land", type=Path, default=DEFAULT_LAND, help="land GeoJSON for meeting filtering")
    parser.add_argument("--sanctions", type=Path, default=DEFAULT_SANCTIONS, help="maritime sanctions CSV")
    parser.add_argument(
        "--meeting-types",
        choices=MEETING_TYPES,
        default="cargo-tanker",
        help="who can be in a meeting: cargo/tanker AIS types 70-89, or all vessels",
    )
    parser.add_argument(
        "--min-offshore-km",
        type=float,
        default=5.0,
        help="minimum distance from land for a meeting in km (default 5)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse flags, fail loudly on a missing recording, and print a one-line summary."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be a positive integer")
    if args.min_offshore_km < 0:
        parser.error("--min-offshore-km must be zero or positive")

    directory = resolve_region_dir(args.region)
    if not directory.is_dir():
        print(f"missing recording folder: {directory}", file=sys.stderr)
        return 2

    payload = build(
        args.region,
        directory,
        args.out,
        hours=args.hours,
        land=args.land,
        sanctions=args.sanctions,
        meeting_types=args.meeting_types,
        min_offshore_km=args.min_offshore_km,
    )
    counts = payload["counts"]
    name = Path(args.region).name or "region"
    print(
        f"{args.region}: {counts['pings']} pings, {counts['vessels']} vessels, "
        f"{counts['silences']} silences, {counts['jumps']} jumps, {counts['clones']} clones, "
        f"{counts['meetings']} meetings, {counts['watchlist_alerts']} watchlist alerts, "
        f"{counts['watchlist_info']} watchlist info "
        f"-> {Path(args.out) / f'live_{name}.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
