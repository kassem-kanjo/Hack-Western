"""Vessel dossier API (#182): one hull, its identity, its last 12 h and its alerts.

The watch screen answers "what happened"; this endpoint answers "who is this". It joins the
files the rest of the API already serves rather than rebuilding anything:

* ``watch.json`` (``api/watch.py``) supplies the hull's name, position, watchlist tag and
  every alert it appears in, including alerts where it is only a contact;
* ``live_<REGION>.json`` (``api/live.py``) supplies the IMO and the cited watchlist entry
  (caption, datasets, url) the live pipeline resolved;
* the recorder's ``*.jsonl.gz`` files supply what only a recording knows: callsign,
  destination, length and the ship's own fixes over the last 12 h.

Everything is read-only and file-backed, following the rest of ``api/``. A hull no file has
ever heard is a 404 JSON; a hull known only to the recording still gets a dossier, because
the map should not require a watch build to name a ship.

The recording scan is the expensive part, so it is parsed once per MMSI per recorder
signature (path + mtime list) and cached; a new hour file or an append to the current one
changes the signature and triggers a re-read.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from darkfleet.api import live as live_api
from darkfleet.api import watch as watch_api
from darkfleet.core.tracks import Ping, VesselInfo, mid_flag, ship_class
from darkfleet.ingest.ais_tracks import iter_files

router = APIRouter(tags=["vessel"])

TRACK_HOURS = 12
RECORDING_WINDOW = timedelta(hours=24)
"""The dossier's window: the same one the watch screen's default filter shows."""

TRACK_MAX_POINTS = 200
"""Hard cap on the drawn track; a fast hull reports more often than the eye can use."""

DEFAULT_AIS_DIR = Path("data/cache/ais")
"""Where ``scripts/record_ais.py`` writes, unless ``DARKFLEET_AIS_DIR`` overrides it.

Matching ``scripts/build_watch.py`` means the dossier works on a fresh checkout that has
never set the variable, while tests can point it at a temporary folder.
"""

_NOT_FOUND = "vessel not found"

# AIS ship-type codes grouped into the words the field table shows. Ranges cover the whole
# ITU table; an unknown code falls back to the coarse class, never to a guess.
_TYPE_LABELS: tuple[tuple[int, int, str], ...] = (
    (30, 30, "Fishing"),
    (31, 32, "Towing"),
    (33, 33, "Dredging"),
    (34, 34, "Diving"),
    (35, 35, "Military"),
    (36, 36, "Sailing"),
    (37, 37, "Pleasure craft"),
    (40, 49, "High-speed craft"),
    (50, 50, "Pilot"),
    (51, 51, "Search and rescue"),
    (52, 52, "Tug"),
    (53, 53, "Port tender"),
    (54, 54, "Anti-pollution"),
    (55, 55, "Law enforcement"),
    (58, 58, "Medical"),
    (60, 69, "Passenger"),
    (70, 79, "Cargo"),
    (80, 89, "Tanker"),
    (90, 99, "Other"),
)

# The maritime identification digits a demo is likely to see. MID is the first three digits
# of a ship MMSI; an unknown prefix is an honest null rather than a guessed flag.
MID_FLAGS: dict[int, str] = {
    205: "Belgium",
    209: "Cyprus",
    210: "Cyprus",
    211: "Germany",
    212: "Cyprus",
    218: "Germany",
    219: "Denmark",
    220: "Denmark",
    224: "Spain",
    225: "Spain",
    226: "France",
    227: "France",
    228: "France",
    232: "United Kingdom",
    233: "United Kingdom",
    234: "United Kingdom",
    235: "United Kingdom",
    236: "Gibraltar",
    237: "Greece",
    238: "Croatia",
    239: "Greece",
    240: "Greece",
    241: "Greece",
    244: "Netherlands",
    245: "Netherlands",
    246: "Netherlands",
    247: "Italy",
    248: "Malta",
    249: "Malta",
    250: "Ireland",
    255: "Portugal",
    256: "Malta",
    257: "Norway",
    258: "Norway",
    259: "Norway",
    261: "Poland",
    263: "Portugal",
    265: "Sweden",
    266: "Sweden",
    271: "Turkey",
    273: "Russia",
    275: "Latvia",
    276: "Estonia",
    277: "Lithuania",
    305: "Antigua and Barbuda",
    308: "Bahamas",
    309: "Bahamas",
    310: "Bermuda",
    311: "Bahamas",
    316: "Canada",
    319: "Cayman Islands",
    338: "United States",
    345: "Mexico",
    351: "Panama",
    352: "Panama",
    353: "Panama",
    354: "Panama",
    355: "Panama",
    356: "Panama",
    357: "Panama",
    366: "United States",
    367: "United States",
    368: "United States",
    369: "United States",
    370: "Panama",
    371: "Panama",
    372: "Panama",
    373: "Panama",
    374: "Panama",
    375: "Saint Vincent and the Grenadines",
    376: "Saint Vincent and the Grenadines",
    377: "Saint Vincent and the Grenadines",
    378: "British Virgin Islands",
    379: "US Virgin Islands",
    403: "Saudi Arabia",
    412: "China",
    413: "China",
    414: "China",
    416: "Taiwan",
    419: "India",
    422: "Iran",
    425: "Iraq",
    428: "Israel",
    431: "Japan",
    432: "Japan",
    440: "South Korea",
    441: "South Korea",
    447: "Kuwait",
    450: "Lebanon",
    461: "Oman",
    466: "Qatar",
    468: "Syria",
    470: "United Arab Emirates",
    477: "Hong Kong",
    503: "Australia",
    512: "New Zealand",
    525: "Indonesia",
    533: "Malaysia",
    538: "Marshall Islands",
    548: "Philippines",
    563: "Singapore",
    564: "Singapore",
    565: "Singapore",
    566: "Singapore",
    574: "Vietnam",
    601: "South Africa",
    603: "Angola",
    605: "Algeria",
    622: "Egypt",
    636: "Liberia",
    642: "Libya",
    657: "Nigeria",
    667: "Sierra Leone",
    671: "Togo",
    677: "Tanzania",
    710: "Brazil",
    725: "Chile",
    735: "Ecuador",
    760: "Peru",
}


@dataclass(frozen=True)
class _Recording:
    """What the recorder knows about one MMSI: its fixes and its latest static record."""

    pings: tuple[Ping, ...] = ()
    info: VesselInfo | None = None


# ---- readers (file-backed, cached like the other routers) --------------------


def recordings_root() -> Path:
    """The recorder cache root, so tests can swap it via the environment."""
    raw = os.environ.get("DARKFLEET_AIS_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_AIS_DIR


def reset_cache() -> None:
    """Drop parsed recordings; tests change DARKFLEET_AIS_DIR between cases."""
    _FILE_INDEX.clear()


def _recording_files(root: Path) -> tuple[Path, ...]:
    """Recent hour files under every region folder, in name order.

    The recorder writes one folder per region and one file per hour. Files untouched for
    longer than ``RECORDING_WINDOW`` are skipped: the dossier shows a 12 h track, and reading
    the whole night's recording on every open was the 5 s first-click delay.
    """
    try:
        directories = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        return ()
    cutoff = datetime.now(UTC).timestamp() - RECORDING_WINDOW.total_seconds()
    files: list[Path] = []
    for directory in directories:
        try:
            files.extend(p for p in sorted(directory.glob("*.jsonl.gz")) if p.stat().st_mtime >= cutoff)
        except OSError:
            continue
    return tuple(files)


# One parsed index per hour file, keyed by (mtime, size). Closed hours never change, so they
# are parsed once; only the file the recorder is appending to is re-read when it grows.
_FileIndex = dict[int, tuple[list[Ping], VesselInfo | None]]
_FILE_INDEX: dict[Path, tuple[tuple[int, int], _FileIndex]] = {}


def _file_index(path: Path) -> _FileIndex:
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _FILE_INDEX.get(path)
    if cached and cached[0] == key:
        return cached[1]
    index: _FileIndex = {}
    for obj in iter_files([path]):
        pings, info = index.get(obj.mmsi, ([], None))
        if isinstance(obj, Ping):
            pings.append(obj)
        else:
            info = _merge_info(info, obj)
        index[obj.mmsi] = (pings, info)
    _FILE_INDEX[path] = (key, index)
    return index


def _recording(mmsi: int) -> _Recording:
    """The recorded fixes and static data for one MMSI, or an empty record."""
    files = _recording_files(recordings_root())
    # Requests run on threads: another request may evict the same entry first, so no KeyError.
    for stale in set(_FILE_INDEX) - set(files):
        _FILE_INDEX.pop(stale, None)
    pings: list[Ping] = []
    info: VesselInfo | None = None
    for path in files:
        try:
            file_pings, file_info = _file_index(path).get(mmsi, ([], None))
        except OSError:
            continue
        pings.extend(file_pings)
        if file_info is not None:
            info = _merge_info(info, file_info)
    pings.sort(key=lambda ping: ping.t)
    return _Recording(pings=tuple(pings), info=info)


def warm_cache() -> None:
    """Parse the recent recording once, so the first dossier opens instantly."""
    for path in _recording_files(recordings_root()):
        try:
            _file_index(path)
        except OSError:
            continue


def _merge_info(current: VesselInfo | None, incoming: VesselInfo) -> VesselInfo:
    """Keep the newest value of each static field; later parts of message 24 fill the rest."""
    if current is None:
        return incoming
    earlier, later = current, incoming
    if (current.last_seen or datetime.min.replace(tzinfo=UTC)) > (
        incoming.last_seen or datetime.min.replace(tzinfo=UTC)
    ):
        earlier, later = incoming, current
    return VesselInfo(
        mmsi=later.mmsi,
        imo=later.imo if later.imo is not None else earlier.imo,
        name=later.name if later.name is not None else earlier.name,
        callsign=later.callsign if later.callsign is not None else earlier.callsign,
        ship_type=later.ship_type if later.ship_type is not None else earlier.ship_type,
        length_m=later.length_m if later.length_m is not None else earlier.length_m,
        destination=later.destination if later.destination is not None else earlier.destination,
        last_seen=later.last_seen if later.last_seen is not None else earlier.last_seen,
    )


def _watch_payload() -> dict[str, Any]:
    """The watch snapshot, sharing ``api/watch.py``'s mtime-keyed parse cache."""
    path = watch_api.watch_path()
    mtime_ns = watch_api._mtime_ns(path)
    if mtime_ns is None:
        return {}
    try:
        return watch_api._load_watch(str(path), mtime_ns)
    except (OSError, ValueError):
        return {}


def _live_matches(mmsi: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Vessel rows, watchlist rows and generated stamps from every live region file."""
    rows: list[dict[str, Any]] = []
    watch_rows: list[dict[str, Any]] = []
    stamps: list[str] = []
    try:
        paths = sorted(live_api.data_dir().glob(f"{live_api.SNAPSHOT_PREFIX}*.json"))
    except OSError:
        return rows, watch_rows, stamps

    for path in paths:
        try:
            payload = live_api._load_snapshot(str(path), path.stat().st_mtime_ns)
        except (OSError, ValueError):
            continue
        stamp = payload.get("generated_at")
        if isinstance(stamp, str):
            stamps.append(stamp)
        for row in live_api._dicts(payload, "vessels"):
            if row.get("mmsi") == mmsi:
                rows.append(row)
        block = payload.get("watchlist")
        if not isinstance(block, dict):
            continue
        for bucket in ("alerts", "info"):
            for row in live_api._dicts(block, bucket):
                if row.get("mmsi") == mmsi:
                    watch_rows.append(row)
    return rows, watch_rows, stamps


# ---- small pure helpers ------------------------------------------------------


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


def _text(value: Any) -> str | None:
    """A non-empty string, stripped, or None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _number(value: Any) -> float | None:
    """A real finite JSON number, or None (booleans are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _integer(value: Any) -> int | None:
    """A real JSON integer, or None."""
    number = _number(value)
    return None if number is None else int(number)


def _imo_value(value: Any) -> int | None:
    """An IMO as an int, accepting the digit string older snapshots wrote."""
    if isinstance(value, str):
        digits = value.strip()
        return int(digits) if digits.isdigit() else None
    return _integer(value)


def _decimate[T](points: Sequence[T], limit: int = TRACK_MAX_POINTS) -> list[T]:
    """Keep at most ``limit`` points, both ends always included, evenly spaced between.

    Selecting by index keeps the work linear and the result independent of how the track
    happened to be ordered; both endpoints survive because they are where the hull came
    from and where it was last seen.
    """
    if len(points) <= limit:
        return list(points)
    last = len(points) - 1
    picks = sorted({round(index * last / (limit - 1)) for index in range(limit)})
    return [points[index] for index in picks]


def _type_label(ship_type: int | None, class_name: str | None) -> str | None:
    """AIS type code -> the word the field table shows; the class is a coarse fallback."""
    if ship_type is not None:
        for low, high, label in _TYPE_LABELS:
            if low <= ship_type <= high:
                return label
    if class_name and class_name != "default":
        return class_name.replace("_", " ").capitalize()
    return None


def _flag(mmsi: int) -> str | None:
    """Flag state from the MMSI's maritime identification digits, or None."""
    mid = mid_flag(mmsi)
    return MID_FLAGS.get(int(mid)) if mid is not None else None


def _last_live_row(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The row with the newest fix, since region files can overlap at the edges."""
    best: dict[str, Any] = {}
    best_t: datetime | None = None
    for row in rows:
        last = row.get("last")
        when = _parse_iso(last.get("t")) if isinstance(last, dict) else None
        if when is None:
            continue
        if best_t is None or when > best_t:
            best, best_t = row, when
    return best


def _position_of(row: dict[str, Any]) -> tuple[datetime, float, float, float | None] | None:
    """``(t, lat, lon, sog)`` from a live vessel row's ``last`` block."""
    last = row.get("last")
    if not isinstance(last, dict):
        return None
    when = _parse_iso(last.get("t"))
    lat = _number(last.get("lat"))
    lon = _number(last.get("lon"))
    if when is None or lat is None or lon is None:
        return None
    return when, lat, lon, _number(last.get("sog"))


# ---- dossier ----------------------------------------------------------------


def _alerts_for(mmsi: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every alert in ``watch.json`` that names this MMSI as the hull or as a contact."""
    found: list[dict[str, Any]] = []
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        return found
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        involves = alert.get("mmsi") == mmsi
        contacts = alert.get("contacts")
        if not involves and isinstance(contacts, list):
            involves = any(isinstance(row, dict) and row.get("mmsi") == mmsi for row in contacts)
        if involves:
            found.append(alert)
    return found


def _alert_ids(alerts: Sequence[dict[str, Any]]) -> list[str]:
    """The ids of the alerts, deduplicated and sorted so repeated builds agree."""
    ids = {alert.get("id") for alert in alerts if isinstance(alert.get("id"), str)}
    return sorted(ids)


def _contact_watchlist(mmsi: int, alerts: Sequence[dict[str, Any]]) -> str | None:
    """The category a watch contact row assigns this MMSI, when no ship row has one."""
    for alert in alerts:
        contacts = alert.get("contacts")
        if not isinstance(contacts, list):
            continue
        for contact in contacts:
            if not isinstance(contact, dict) or contact.get("mmsi") != mmsi:
                continue
            category = _text(contact.get("watchlist"))
            if category is not None:
                return category
    return None


def _watchlist_block(
    mmsi: int,
    uses: Sequence[dict[str, Any]],
    ship: dict[str, Any],
    alerts: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """The watchlist value with its citation: category, list name, datasets and url.

    The live pipeline is the only source that resolved an entry against the sanctions
    file, so its row wins. A watch build without that row still knows the category; the
    citation fields then stay null rather than being invented.
    """
    row = uses[0] if uses else {}
    entry = row.get("entry") if isinstance(row.get("entry"), dict) else {}
    category = _text(row.get("category")) or _text(ship.get("watchlist")) or _contact_watchlist(mmsi, alerts)
    if category is None:
        return None

    datasets = entry.get("datasets")
    return {
        "category": category,
        "list_name": _text(entry.get("caption")),
        "datasets": [value for value in datasets if isinstance(value, str)]
        if isinstance(datasets, list)
        else [],
        "url": _text(entry.get("url")),
    }


def _own_trail(
    mmsi: int, alerts: Sequence[dict[str, Any]], start: datetime, end: datetime
) -> list[list[Any]]:
    """Fallback track from the watch snapshot's own trails, when no recording is present.

    Only alerts whose hull is this MMSI carry its trail; a contact's row is a single fix.
    """
    points: dict[tuple[float, float, str], None] = {}
    for alert in alerts:
        if alert.get("mmsi") != mmsi:
            continue
        trail = alert.get("trail")
        if not isinstance(trail, list):
            continue
        for point in trail:
            if not isinstance(point, list) or len(point) < 3:
                continue
            lon, lat, stamp = _number(point[0]), _number(point[1]), point[2]
            when = _parse_iso(stamp)
            if lon is None or lat is None or when is None or not (start <= when <= end):
                continue
            points[(round(lon, 5), round(lat, 5), _iso(when))] = None
    ordered = sorted(points, key=lambda key: (key[2], key[0], key[1]))
    return [[lon, lat, stamp] for lon, lat, stamp in _decimate(ordered)]


def _dossier(mmsi: int) -> dict[str, Any] | None:
    """Build the dossier, or None when no file has ever heard this MMSI."""
    payload = _watch_payload()
    ships = payload.get("ships")
    ship = (
        next(
            (row for row in ships if isinstance(row, dict) and row.get("mmsi") == mmsi),
            {},
        )
        if isinstance(ships, list)
        else {}
    )
    alerts = _alerts_for(mmsi, payload)
    own_alerts = [alert for alert in alerts if alert.get("mmsi") == mmsi]

    live_rows, live_watch, live_stamps = _live_matches(mmsi)
    live = _last_live_row(live_rows)

    recording = _recording(mmsi)
    info = recording.info
    ship_type = _integer(live.get("ship_type"))
    if ship_type is None and info is not None:
        ship_type = info.ship_type
    class_name = _text(live.get("class")) or _text(ship.get("class"))
    if class_name is None:
        class_name = ship_class(ship_type) if ship_type is not None else None

    # Latest fix: the recording is freshest, then the live snapshot, then the watch file,
    # then an alert's own last fix. Ties keep the earlier source in this list.
    fixes: list[tuple[datetime, float, float, float | None, float | None]] = []

    if recording.pings:
        ping = recording.pings[-1]
        fixes.append((ping.t, ping.lat, ping.lon, ping.sog, ping.cog))

    live_position = _position_of(live)
    if live_position is not None:
        fixes.append((*live_position, None))

    watch_when = _parse_iso(ship.get("t"))
    watch_lat = _number(ship.get("lat"))
    watch_lon = _number(ship.get("lon"))
    if watch_when is not None and watch_lat is not None and watch_lon is not None:
        fixes.append((watch_when, watch_lat, watch_lon, _number(ship.get("sog")), None))

    for alert in own_alerts:
        last = alert.get("last")
        if not isinstance(last, dict):
            continue
        when = _parse_iso(last.get("t"))
        lat, lon = _number(last.get("lat")), _number(last.get("lon"))
        if when is not None and lat is not None and lon is not None:
            fixes.append((when, lat, lon, _number(last.get("sog")), _number(last.get("cog"))))

    if not fixes:
        return None

    last = max(fixes, key=lambda fix: fix[0])
    anchor = max(
        [last[0]]
        + [moment for moment in (_parse_iso(payload.get("generated_at")),) if moment is not None]
        + [moment for moment in (_parse_iso(stamp) for stamp in live_stamps) if moment is not None]
    )
    start = anchor - timedelta(hours=TRACK_HOURS)

    window_pings = [ping for ping in recording.pings if start <= ping.t <= anchor]
    if window_pings:
        track = [[round(ping.lon, 5), round(ping.lat, 5), _iso(ping.t)] for ping in _decimate(window_pings)]
        first_seen = _iso(window_pings[0].t)
    else:
        track = _own_trail(mmsi, own_alerts, start, anchor)
        first_seen = track[0][2] if track else None

    name = (
        _text(ship.get("name"))
        or _text(live.get("name"))
        or _text(info.name if info is not None else None)
        or next((_text(alert.get("name")) for alert in own_alerts if _text(alert.get("name"))), None)
    )

    imo = _imo_value(live.get("imo"))
    if imo is None and info is not None:
        imo = info.imo
    if imo is None:
        for alert in own_alerts:
            evidence = alert.get("evidence")
            if isinstance(evidence, dict):
                imo = _imo_value(evidence.get("imo"))
                if imo is not None:
                    break

    return {
        "mmsi": mmsi,
        "name": name,
        "imo": imo,
        "callsign": info.callsign if info is not None else None,
        "type": _type_label(ship_type, class_name),
        "class": class_name,
        "flag": _flag(mmsi),
        "length_m": info.length_m if info is not None else None,
        "destination": info.destination if info is not None else None,
        "watchlist": _watchlist_block(mmsi, live_watch, ship, alerts),
        "last": {
            "lat": last[1],
            "lon": last[2],
            "t": _iso(last[0]),
            "sog": last[3],
            "cog": last[4],
        },
        "track": track,
        "alerts": _alert_ids(alerts),
        "stats": {
            "pings_12h": len(window_pings),
            "first_seen": first_seen,
            "last_seen": _iso(last[0]),
        },
    }


@router.get("/api/vessel/{mmsi}")
def get_vessel(mmsi: int) -> dict[str, Any]:
    """One hull's dossier, or a 404 JSON body when no snapshot or recording knows it."""
    payload = _dossier(mmsi)
    if payload is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return payload
