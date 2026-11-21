"""Shared contract for the Baltic engine (issue #72-#76): pings, vessel info, coverage, events.

Every lane codes against these types so parallel workers never disagree about shapes. Pure: no I/O.
Times are timezone-aware UTC datetimes. Positions are WGS84 degrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

EventKind = Literal["silence", "jump", "clone", "meeting", "dark_meeting", "spoof"]


@dataclass(frozen=True)
class Ping:
    """One AIS position report."""

    mmsi: int
    t: datetime
    lat: float
    lon: float
    sog: float | None = None  # knots
    cog: float | None = None  # degrees
    heading: int | None = None  # degrees, 511 = not available (stored as None)
    nav_status: int | None = None  # AIS navigational status code (1 at anchor, 5 moored, ...)


@dataclass(frozen=True)
class VesselInfo:
    """Latest static/voyage data for one MMSI (AIS message 5 / 24)."""

    mmsi: int
    imo: int | None = None
    name: str | None = None
    callsign: str | None = None
    ship_type: int | None = None  # AIS type code (70-79 cargo, 80-89 tanker, 30 fishing, ...)
    length_m: float | None = None
    destination: str | None = None
    last_seen: datetime | None = None


@dataclass(frozen=True)
class CoverageMinute:
    """How many messages the recorder received in one UTC minute (all vessels in the box)."""

    minute: datetime
    n_msgs: int


@dataclass(frozen=True)
class Event:
    """One finding with the numbers that justify it.

    severity: 0-1, how strongly physics supports the finding (not guilt).
    evidence: plain numbers (km, knots, minutes, counts) the UI shows verbatim.
    explanation: one plain-English sentence a judge can read.
    """

    kind: EventKind
    mmsi: int
    start: datetime
    end: datetime
    lat: float
    lon: float
    severity: float
    explanation: str
    other_mmsi: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def ship_class(ship_type: int | None) -> str:
    """Map an AIS ship-type code to the speed-table classes used in core.speeds."""
    if ship_type is None:
        return "default"
    if 80 <= ship_type <= 89:
        return "tanker"
    if 70 <= ship_type <= 79:
        return "cargo"
    if ship_type == 30:
        return "fishing"
    if 40 <= ship_type <= 49:
        # High-speed craft (fast ferries such as EXPRESS 5 and TRANEN) genuinely sustain 35-40 kn.
        return "hsc"
    if 50 <= ship_type <= 59:
        # Pilot, search-and-rescue, tug and other service craft: fast but not ferry-fast.
        return "service"
    if 60 <= ship_type <= 69:
        return "passenger"
    return "default"


def mid_flag(mmsi: int) -> str | None:
    """Maritime Identification Digits (first 3 digits of a 9-digit MMSI); caller maps MID to a flag."""
    s = str(mmsi)
    return s[:3] if len(s) == 9 and s[0] in "234567" else None
