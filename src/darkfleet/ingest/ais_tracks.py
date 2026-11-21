"""Parse recorded aisstream lines into the Baltic engine's pure types.

The recorder (``scripts/record_ais.py``) appends the raw websocket payload, one JSON object
per line, so this module is the only place that knows aisstream's field names. Everything it
returns is a :class:`~darkfleet.core.tracks.Ping` or
:class:`~darkfleet.core.tracks.VesselInfo`, which keeps the detectors independent of the feed
and independent of storage.

Malformed lines are expected in a multi-hour recording (partial writes, schema changes at the
provider), so parsing never raises: a line we cannot understand is simply skipped. AIS also
encodes "not available" as sentinel numbers, e.g. ``TrueHeading == 511``, which are normalised
to ``None`` here so no downstream rule has to remember the sentinels.

Pure: no I/O happens in ``parse_line`` or ``coverage``. ``iter_files`` is the one I/O door.
"""

from __future__ import annotations

import gzip
import heapq
import json
import re
import zlib
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from darkfleet.core.tracks import CoverageMinute, Ping, VesselInfo

__all__ = ["coverage", "iter_files", "parse_line"]

# aisstream stamps Metadata.time_utc as "2026-11-21 04:59:57.096836168 +0000 UTC". The
# fraction is nanoseconds, which datetime cannot hold, so it is truncated to microseconds
# rather than rounded (rounding could carry a whole second and reorder a burst of pings).
_TIME_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"\s*(?P<offset>[+-]\d{2}:?\d{2}|Z)"
    r"(?:\s+UTC)?$"
)

# Heading 511 is the AIS "not available" sentinel; the contract stores it as None.
_HEADING_UNAVAILABLE = 511

# AIS courses and speeds at or above these values are sentinels, not measurements: SOG 102.3
# ("not available") and COG 360 mean the receiver should not invent a course or speed from them.
_SPEED_UNAVAILABLE = 102.2
_COURSE_UNAVAILABLE = 360.0

_POSITION_TYPES = frozenset({"PositionReport", "StandardClassBPositionReport"})


def _numeric(value: Any) -> float | None:
    """Return ``value`` as a float when it is a real JSON number, else None.

    ``bool`` is a subclass of ``int`` in Python, and aisstream does send booleans in numeric
    fields by mistake; treating ``true`` as 1.0 would create fake positions, so reject it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _integer(value: Any) -> int | None:
    """Return ``value`` as an int for code-like fields (ship type, nav status), else None."""
    number = _numeric(value)
    return None if number is None else int(number)


def _text(value: Any) -> str | None:
    """Strip AIS's fixed-width padding; empty padding means the field was not set."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _mid(body: dict[str, Any]) -> int | None:
    """AIS message-24 variants carry the user id as ``UserID``; both parts use it."""
    return _integer(body.get("UserID"))


def _parse_time(value: Any) -> datetime | None:
    """Parse aisstream's UTC stamp, truncating nanoseconds, or None if it is unusable."""
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if match is None:
        return None
    fraction = (match["frac"] or "")[:6].ljust(6, "0")
    offset = match["offset"]
    if offset == "Z":
        offset = "+00:00"
    elif ":" not in offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    try:
        parsed = datetime.fromisoformat(f"{match['base'].replace(' ', 'T')}.{fraction}{offset}")
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _mmsi(meta: dict[str, Any], body: dict[str, Any]) -> int | None:
    """Prefer Metadata.MMSI (the feed's key) and fall back to the body's UserID."""
    return _integer(meta.get("MMSI")) or _mid(body)


def _ping(meta: dict[str, Any], body: dict[str, Any], t: datetime) -> Ping | None:
    """Build a Ping from a class-A or class-B position body; None if position is missing."""
    mmsi = _mmsi(meta, body)
    lat = _numeric(body.get("Latitude"))
    lon = _numeric(body.get("Longitude"))
    if mmsi is None or lat is None or lon is None:
        return None
    heading = _integer(body.get("TrueHeading"))
    if heading == _HEADING_UNAVAILABLE:
        heading = None
    sog = _numeric(body.get("Sog"))
    if sog is not None and sog >= _SPEED_UNAVAILABLE:
        sog = None
    cog = _numeric(body.get("Cog"))
    if cog is not None and cog >= _COURSE_UNAVAILABLE:
        cog = None
    return Ping(
        mmsi=mmsi,
        t=t,
        lat=lat,
        lon=lon,
        sog=sog,
        cog=cog,
        heading=heading,
        nav_status=_integer(body.get("NavigationalStatus")),
    )


def _length(dimension: Any) -> float | None:
    """AIS dimensions are bow/stern (A/B) plus port/starboard (C/D); A+B is overall length.

    A zero dimension means the vessel has not programmed it, so 0 is reported as unknown
    rather than as a real length of zero metres.
    """
    if not isinstance(dimension, dict):
        return None
    bow = _numeric(dimension.get("A")) or 0.0
    stern = _numeric(dimension.get("B")) or 0.0
    total = bow + stern
    return total if total > 0 else None


def _ship_static_data(meta: dict[str, Any], body: dict[str, Any], t: datetime) -> VesselInfo | None:
    """Message 5: one complete static/voyage record, emitted every few minutes."""
    mmsi = _mmsi(meta, body)
    if mmsi is None:
        return None
    imo = _integer(body.get("ImoNumber"))
    return VesselInfo(
        mmsi=mmsi,
        imo=imo if imo else None,  # 0 is the AIS "not available" sentinel for IMO
        name=_text(body.get("Name")),
        callsign=_text(body.get("CallSign")),
        ship_type=_integer(body.get("Type")),
        length_m=_length(body.get("Dimension")),
        destination=_text(body.get("Destination")),
        last_seen=t,
    )


def _static_data_report(meta: dict[str, Any], body: dict[str, Any], t: datetime) -> VesselInfo | None:
    """Message 24: class-B static data, split across part A (name) and part B (the rest).

    Either part may arrive alone, so every field is optional. Class-B units do not broadcast
    an IMO number and rarely a destination, hence those stay None.
    """
    mmsi = _mmsi(meta, body)
    if mmsi is None:
        return None
    return VesselInfo(
        mmsi=mmsi,
        name=_text(body.get("ShipName")),
        callsign=_text(body.get("CallSign")),
        ship_type=_integer(body.get("ShipType")),
        length_m=_length(body.get("Dimension")),
        last_seen=t,
    )


def parse_line(line: str) -> Ping | VesselInfo | None:
    """Parse one raw aisstream JSON line.

    Returns a Ping for position messages (class A and class B), a VesselInfo for static
    messages, and None for message types the Baltic engine does not use or for anything
    malformed. Never raises: a corrupt recording must still load, minus the bad lines.
    """
    if not line or not line.strip():
        return None
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None

    meta = record.get("MetaData")
    message = record.get("Message")
    message_type = record.get("MessageType")
    if not isinstance(meta, dict) or not isinstance(message, dict):
        return None
    body = message.get(message_type)
    if not isinstance(body, dict):
        return None

    t = _parse_time(meta.get("time_utc"))
    if t is None:
        return None

    if message_type in _POSITION_TYPES:
        return _ping(meta, body, t)
    if message_type == "ShipStaticData":
        return _ship_static_data(meta, body, t)
    if message_type == "StaticDataReport":
        return _static_data_report(meta, body, t)
    return None


def _recording_files(paths: Iterable[str | Path]) -> list[Path]:
    """Expand directories to their ``*.jsonl.gz`` files, deduplicated, in filename order.

    The recorder names files ``YYYYMMDDTHH.jsonl.gz``, so filename order is hour order; that
    keeps a full-day merge to files already in sequence while each file is buffered once.
    """
    found: dict[Path, None] = {}
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.glob("*.jsonl.gz")):
                found[child] = None
        elif path.is_file() and path.name.endswith(".jsonl.gz"):
            found[path] = None
    return sorted(found)


def _sort_key(obj: Ping | VesselInfo) -> datetime:
    """Every parsed object carries the recording time: when parsed, that is never None."""
    return obj.t if isinstance(obj, Ping) else obj.last_seen or datetime.min.replace(tzinfo=UTC)


def _parsed_file(path: Path, order: int) -> Iterator[tuple[datetime, int, int, Ping | VesselInfo]]:
    """Read one file, sorted by time, as mergeable ``(time, file, line, object)`` tuples.

    A single hour of Baltic traffic fits comfortably in memory, and sorting here absorbs the
    small arrival-order jitter the live socket leaves in the recording.
    """
    rows: list[tuple[datetime, int, Ping | VesselInfo]] = []
    # The recorder appends to the current hour's file while we read it, so its tail is often a
    # half-written deflate block. Keep every complete line before the break instead of failing.
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                obj = parse_line(line)
                if obj is not None:
                    rows.append((_sort_key(obj), index, obj))
    except (EOFError, zlib.error, OSError):
        pass
    rows.sort(key=lambda row: (row[0], row[1]))
    for t, index, obj in rows:
        yield t, order, index, obj


def iter_files(paths: Iterable[str | Path]) -> Iterator[Ping | VesselInfo]:
    """Yield every parsed object from the given recordings in time order.

    ``paths`` may mix directories and individual ``.jsonl.gz`` files. Files are read one at a
    time and merged by timestamp, so a caller can build a whole-day track history without
    holding the day's raw lines (only one hour's parsed objects) in memory.
    """
    files = _recording_files(paths)
    streams = [_parsed_file(path, order) for order, path in enumerate(files)]
    for _, _, _, obj in heapq.merge(*streams):
        yield obj


def _minute(t: datetime) -> datetime:
    """Floor to the UTC minute; naive times are treated as UTC like everywhere else."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).replace(second=0, microsecond=0)


def coverage(times: Iterable[datetime]) -> list[CoverageMinute]:
    """Count messages per UTC minute, including minutes with zero messages.

    The detector needs to tell a vessel's silence from the recorder's own outages, so holes
    between the first and last observed minute are materialised with ``n_msgs == 0`` rather
    than omitted. Leading and trailing minutes with no traffic are not invented: outside the
    recording we know nothing.
    """
    counts: dict[datetime, int] = {}
    for t in times:
        minute = _minute(t)
        counts[minute] = counts.get(minute, 0) + 1
    if not counts:
        return []

    first, last = min(counts), max(counts)
    out: list[CoverageMinute] = []
    minute = first
    while minute <= last:
        out.append(CoverageMinute(minute=minute, n_msgs=counts.get(minute, 0)))
        minute += timedelta(minutes=1)
    return out
