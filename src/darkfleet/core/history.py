"""Pure shaping of GFW encounter and loitering events into the Hormuz history snapshot.

The snapshot builder fetches events through the ingest client, but everything that decides what
the written file *means* lives here: the item shapes the UI reads and the 0.1-degree density grid
the map draws. Keeping it pure means the shaping is tested without a token, a cache or a network.

Events are the frozen dataclasses of :mod:`darkfleet.ingest.gfw`, but they are read by attribute
rather than by ``isinstance``: importing that module would pull ``httpx`` into ``core``, which is
meant to stay free of I/O. The two event families share one item stream because the map pools them
on the same grid, so one mapper has to recognise both.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any


def _iso(value: datetime) -> str:
    """UTC ISO-8601 with the ``Z`` suffix the other snapshots use."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _vessel(name: object, mmsi: object, flag: object, vessel_type: object) -> dict[str, Any]:
    """The vessel block the UI expects; ``mmsi`` is GFW's ``ssvid`` field."""
    return {"name": name, "mmsi": mmsi, "flag": flag, "type": vessel_type}


def _other_present(event: Any) -> bool:
    """True when the encounter names a second vessel at all, even if every identity field is null.

    An encounter with no partner identity is still a two-ship event in GFW, so the id alone counts;
    omitting the block entirely is reserved for records where the partner is genuinely absent.
    """
    fields = (
        "encountered_vessel_id",
        "encountered_name",
        "encountered_mmsi",
        "encountered_flag",
        "encountered_vessel_type",
    )
    return any(getattr(event, field, None) is not None for field in fields)


def _encounter_item(event: Any) -> dict[str, Any]:
    """One encounter in the history file: position, both vessels, and the median separation."""
    item: dict[str, Any] = {
        "id": event.id,
        "start": _iso(event.start),
        "end": _iso(event.end),
        "lat": event.lat,
        "lon": event.lon,
        "vessel": _vessel(
            event.vessel_name,
            event.mmsi,
            event.flag,
            event.vessel_type,
        ),
        "median_distance_km": event.median_distance_km,
        "median_speed_kn": event.median_speed_knots,
    }
    if _other_present(event):
        item["other"] = _vessel(
            event.encountered_name,
            event.encountered_mmsi,
            event.encountered_flag,
            getattr(event, "encountered_vessel_type", None),
        )
    return item


def _hours(event: Any) -> float | None:
    """Dwell time, falling back to the event's own clock when GFW omits its duration field."""
    value = event.duration_hours
    if value is not None:
        return float(value)
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    if isinstance(start, datetime) and isinstance(end, datetime):
        return (end - start).total_seconds() / 3600.0
    return None


def _loitering_item(event: Any) -> dict[str, Any]:
    """One loitering event: the dwell time and how far from shore it happened."""
    return {
        "id": event.id,
        "start": _iso(event.start),
        "end": _iso(event.end),
        "lat": event.lat,
        "lon": event.lon,
        "hours": _hours(event),
        "vessel": _vessel(
            event.vessel_name,
            event.mmsi,
            event.flag,
            event.vessel_type,
        ),
        "distance_from_shore_km": getattr(event, "distance_from_shore_km", None),
    }


def to_history_items(events: Iterable[Any]) -> list[dict[str, Any]]:
    """Map encounter and loitering records to the JSON-ready history items.

    The family is recognised by the fields the record carries, not by its class: an encounter has
    a second vessel, while a loitering event has a dwell duration. Input order is preserved so the
    snapshot is reproducible, and any other object is rejected loudly rather than silently written
    in the wrong shape.
    """
    items: list[dict[str, Any]] = []
    for event in events:
        if hasattr(event, "encountered_vessel_id"):
            items.append(_encounter_item(event))
        elif hasattr(event, "duration_hours"):
            items.append(_loitering_item(event))
        else:
            raise TypeError(
                f"unsupported history event {type(event).__name__}: expected an encounter or loitering record"
            )
    return items


def _lat_lon(item: object) -> tuple[float, float] | None:
    """Read a position from either a mapped history item or a raw event; None if absent."""
    if isinstance(item, dict):
        lat, lon = item.get("lat"), item.get("lon")
    else:
        lat, lon = getattr(item, "lat", None), getattr(item, "lon", None)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _cell_center(value: float, cell_deg: float) -> float:
    """Centre of the grid cell containing ``value``, rounded away from float noise.

    The grid is anchored at multiples of ``cell_deg`` so the same position always lands in the
    same square regardless of the order events arrive in.
    """
    return round((math.floor(value / cell_deg) + 0.5) * cell_deg, 6)


def hotspots(items: Iterable[object], cell_deg: float = 0.1, top: int = 10) -> list[dict[str, Any]]:
    """Densest cells across both event kinds, as ``{lat, lon, n}`` at each cell's centre.

    Encounters and loitering events are pooled because the map answers one question: where did
    vessels bunch up? Ties break on latitude then longitude so repeated runs over the same data
    produce byte-identical snapshots, and ``top`` caps the list the UI has to draw.
    """
    if cell_deg <= 0:
        raise ValueError("cell_deg must be positive")

    counts: Counter[tuple[float, float]] = Counter()
    for item in items:
        position = _lat_lon(item)
        if position is None:
            continue
        lat, lon = position
        counts[(_cell_center(lat, cell_deg), _cell_center(lon, cell_deg))] += 1

    ranked = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0][0], entry[0][1]))
    return [{"lat": lat, "lon": lon, "n": n} for (lat, lon), n in ranked[:top]]
