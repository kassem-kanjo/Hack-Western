"""Global Fishing Watch (GFW) v3 client: typed records, pure parsers, disk cache.

This module holds the record types shared with ``darkfleet.store``. The HTTP client and the
parsers are added below them. Records are plain frozen dataclasses so the store and the core
never depend on the raw JSON shape.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx

T = TypeVar("T")

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"
GAPS_DATASET = "public-global-gaps-events:latest"
ENCOUNTERS_DATASET = "public-global-encounters-events:latest"
LOITERING_DATASET = "public-global-loitering-events:latest"
SAR_DATASET = "public-global-sar-presence:latest"


@dataclass(frozen=True, slots=True)
class GapEvent:
    """An AIS 'gap' (the transponder went silent). ``start``/``end`` are timezone-aware UTC."""

    id: str
    vessel_id: str
    mmsi: str | None
    vessel_name: str | None
    flag: str | None
    vessel_type: str | None
    start: datetime
    end: datetime
    off_lat: float
    off_lon: float
    on_lat: float
    on_lon: float
    duration_hours: float
    implied_speed_knots: float | None
    distance_km: float | None
    intentional_disabling: bool


@dataclass(frozen=True, slots=True)
class EncounterEvent:
    """Two vessels at sea close together. The encountered vessel fields describe the other ship."""

    id: str
    start: datetime
    end: datetime
    vessel_id: str
    mmsi: str | None
    vessel_name: str | None
    flag: str | None
    vessel_type: str | None
    encountered_vessel_id: str | None
    encountered_mmsi: str | None
    encountered_name: str | None
    encountered_flag: str | None
    lat: float
    lon: float
    median_distance_km: float | None
    median_speed_knots: float | None
    encounter_type: str | None
    potential_risk: bool


@dataclass(frozen=True, slots=True)
class SarCellRow:
    """One radar detection cell (about 1 km at HIGH resolution) for one UTC day.

    ``vessel_ids`` is the raw ``vesselIDs`` value from the 4Wings row (the fixture reports 1 for
    every unmatched cell). It is kept as given; interpretation belongs to ``core/``.
    """

    day: date
    lat: float
    lon: float
    detections: int
    vessel_ids: int


@dataclass(frozen=True, slots=True)
class LoiteringEvent:
    """A vessel lingering in one place longer than normal for its type."""

    id: str
    start: datetime
    end: datetime
    vessel_id: str
    mmsi: str | None
    vessel_name: str | None
    flag: str | None
    vessel_type: str | None
    lat: float
    lon: float
    duration_hours: float | None
    average_speed_knots: float | None
    total_distance_km: float | None


def _entry_id(entry: dict) -> str:
    value = entry.get("id")
    return str(value) if value is not None else "<unknown>"


def _required(mapping: dict, key: str, entry_id: str, kind: str) -> Any:
    value = mapping.get(key)
    if value is None:
        raise ValueError(f"{kind} entry {entry_id!r} missing required field {key!r}")
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; the API uses a ``Z`` suffix."""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_gap_entries(entries: list[dict]) -> list[GapEvent]:
    """Map the raw ``/events`` gap list to typed records."""
    parsed: list[GapEvent] = []
    for entry in entries:
        entry_id = _entry_id(entry)
        vessel = _required(entry, "vessel", entry_id, "gap")
        gap = _required(entry, "gap", entry_id, "gap")
        off = _required(gap, "offPosition", entry_id, "gap")
        on = _required(gap, "onPosition", entry_id, "gap")
        parsed.append(
            GapEvent(
                id=_required(entry, "id", entry_id, "gap"),
                vessel_id=_required(vessel, "id", entry_id, "gap"),
                mmsi=_optional_str(vessel.get("ssvid")),
                vessel_name=_optional_str(vessel.get("name")),
                flag=_optional_str(vessel.get("flag")),
                vessel_type=_optional_str(vessel.get("type")),
                start=_parse_datetime(_required(entry, "start", entry_id, "gap")),
                end=_parse_datetime(_required(entry, "end", entry_id, "gap")),
                off_lat=float(_required(off, "lat", entry_id, "gap")),
                off_lon=float(_required(off, "lon", entry_id, "gap")),
                on_lat=float(_required(on, "lat", entry_id, "gap")),
                on_lon=float(_required(on, "lon", entry_id, "gap")),
                duration_hours=float(_required(gap, "durationHours", entry_id, "gap")),
                implied_speed_knots=_optional_float(gap.get("impliedSpeedKnots")),
                distance_km=_optional_float(gap.get("distanceKm")),
                intentional_disabling=bool(gap.get("intentionalDisabling", False)),
            )
        )
    return parsed


def parse_encounter_entries(entries: list[dict]) -> list[EncounterEvent]:
    """Map the raw ``entries`` list of an encounters response to typed records."""
    parsed: list[EncounterEvent] = []
    for entry in entries:
        entry_id = _entry_id(entry)
        vessel = _required(entry, "vessel", entry_id, "encounter")
        position = _required(entry, "position", entry_id, "encounter")
        encounter = entry.get("encounter") or {}
        other = encounter.get("vessel") or {}
        parsed.append(
            EncounterEvent(
                id=_required(entry, "id", entry_id, "encounter"),
                start=_parse_datetime(_required(entry, "start", entry_id, "encounter")),
                end=_parse_datetime(_required(entry, "end", entry_id, "encounter")),
                vessel_id=_required(vessel, "id", entry_id, "encounter"),
                mmsi=_optional_str(vessel.get("ssvid")),
                vessel_name=_optional_str(vessel.get("name")),
                flag=_optional_str(vessel.get("flag")),
                vessel_type=_optional_str(vessel.get("type")),
                encountered_vessel_id=_optional_str(other.get("id")),
                encountered_mmsi=_optional_str(other.get("ssvid")),
                encountered_name=_optional_str(other.get("name")),
                encountered_flag=_optional_str(other.get("flag")),
                lat=float(_required(position, "lat", entry_id, "encounter")),
                lon=float(_required(position, "lon", entry_id, "encounter")),
                median_distance_km=_optional_float(encounter.get("medianDistanceKilometers")),
                median_speed_knots=_optional_float(encounter.get("medianSpeedKnots")),
                encounter_type=_optional_str(encounter.get("type")),
                potential_risk=bool(encounter.get("potentialRisk", False)),
            )
        )
    return parsed


def parse_loitering_entries(entries: list[dict]) -> list[LoiteringEvent]:
    """Map the raw loitering list to typed records, tolerating absent sub-keys."""
    parsed: list[LoiteringEvent] = []
    for entry in entries:
        entry_id = _entry_id(entry)
        vessel = _required(entry, "vessel", entry_id, "loitering")
        position = _required(entry, "position", entry_id, "loitering")
        loitering = entry.get("loitering") or {}
        parsed.append(
            LoiteringEvent(
                id=_required(entry, "id", entry_id, "loitering"),
                start=_parse_datetime(_required(entry, "start", entry_id, "loitering")),
                end=_parse_datetime(_required(entry, "end", entry_id, "loitering")),
                vessel_id=_required(vessel, "id", entry_id, "loitering"),
                mmsi=_optional_str(vessel.get("ssvid")),
                vessel_name=_optional_str(vessel.get("name")),
                flag=_optional_str(vessel.get("flag")),
                vessel_type=_optional_str(vessel.get("type")),
                lat=float(_required(position, "lat", entry_id, "loitering")),
                lon=float(_required(position, "lon", entry_id, "loitering")),
                duration_hours=_optional_float(loitering.get("timeInHours")),
                average_speed_knots=_optional_float(loitering.get("averageSpeedKnots")),
                total_distance_km=_optional_float(loitering.get("totalDistanceKm")),
            )
        )
    return parsed


def parse_sar_report(payload: dict) -> list[SarCellRow]:
    """Flatten the 4Wings report; the dataset-name key is not hardcoded."""
    rows: list[SarCellRow] = []
    for group in payload.get("entries", []):
        for dataset_rows in group.values():
            for row in dataset_rows:
                rows.append(
                    SarCellRow(
                        day=date.fromisoformat(row["date"]),
                        lat=float(row["lat"]),
                        lon=float(row["lon"]),
                        detections=int(row["detections"]),
                        vessel_ids=int(row.get("vesselIDs", 0)),
                    )
                )
    return rows


class GfwError(RuntimeError):
    """A GFW request failed permanently. Messages never include the token."""


class GfwClient:
    """Thin, cached GFW v3 client. Pass ``transport`` for offline tests."""

    def __init__(
        self,
        token: str,
        cache_dir: str | Path = "data/cache/gfw",
        *,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_tries: int = 4,
        backoff_base: float = 1.0,
    ) -> None:
        if not token:
            raise ValueError("GFW API token is required")
        self._cache_dir = Path(cache_dir)
        self._sleep = sleep
        self._max_tries = max_tries
        self._backoff_base = backoff_base
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=60,
        )

    def _request(self, method: str, path: str, params: dict, body: dict) -> dict:
        key_material = json.dumps(
            {"method": method, "path": path, "params": params, "body": body}, sort_keys=True
        )
        cache_path = self._cache_dir / f"{hashlib.sha256(key_material.encode()).hexdigest()}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        last_status: int | None = None
        for attempt in range(self._max_tries):
            try:
                response = self._client.request(method, path, params=params, json=body)
            except httpx.TransportError:
                last_status = None
            else:
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    data = response.json()
                    self._cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(data), encoding="utf-8")
                    return data
                if response.status_code != 429 and response.status_code < 500:
                    raise GfwError(f"GFW request failed: HTTP {response.status_code} for {path}")
            if attempt + 1 < self._max_tries:
                self._sleep(self._backoff_base * (2**attempt))

        status = f"HTTP {last_status}" if last_status is not None else "transport error"
        raise GfwError(f"GFW request failed after {self._max_tries} attempts: {status} for {path}")

    def _events(
        self,
        dataset: str,
        region: dict,
        start: date,
        end: date,
        parser: Callable[[list[dict]], list[T]],
    ) -> list[T]:
        entries: list[dict] = []
        offset = 0
        while True:
            payload = self._request(
                "POST",
                "/events",
                {"limit": 200, "offset": offset},
                {
                    "datasets": [dataset],
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "geometry": region,
                },
            )
            page = payload.get("entries") or []
            entries.extend(page)
            next_offset = payload.get("nextOffset")
            if not next_offset or not page or next_offset <= offset:
                break
            offset = next_offset
        return parser(entries)

    def gaps(self, region: dict, start: date, end: date) -> list[GapEvent]:
        return self._events(GAPS_DATASET, region, start, end, parse_gap_entries)

    def encounters(self, region: dict, start: date, end: date) -> list[EncounterEvent]:
        return self._events(ENCOUNTERS_DATASET, region, start, end, parse_encounter_entries)

    def loitering(self, region: dict, start: date, end: date) -> list[LoiteringEvent]:
        return self._events(LOITERING_DATASET, region, start, end, parse_loitering_entries)

    def sar_cells(
        self,
        region: dict,
        start: date,
        end: date,
        unmatched_only: bool = True,
    ) -> list[SarCellRow]:
        params = {
            "spatial-resolution": "HIGH",
            "temporal-resolution": "DAILY",
            "datasets[0]": SAR_DATASET,
            "date-range": f"{start.isoformat()},{end.isoformat()}",
            "format": "JSON",
        }
        if unmatched_only:
            params["filters[0]"] = "matched='false'"
        payload = self._request("POST", "/4wings/report", params, {"geojson": region})
        return parse_sar_report(payload)
