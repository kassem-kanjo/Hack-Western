"""SQLite implementation of :class:`darkfleet.store.repository.Repository`.

Timestamps are stored as UTC ISO-8601 strings (``datetime.isoformat()``) and dates as
``YYYY-MM-DD``, which makes inclusive date filtering a plain string comparison on the
date prefix. Every write is an ``INSERT ... ON CONFLICT DO UPDATE`` inside one
transaction, so re-running an ingest neither duplicates nor renumbers rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow
from darkfleet.store.repository import DarkHoursDay, PositionRow

__all__ = ["SqliteRepo"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gap_events (
    id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    vessel_id TEXT NOT NULL,
    mmsi TEXT,
    vessel_name TEXT,
    flag TEXT,
    vessel_type TEXT,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL,
    off_lat REAL,
    off_lon REAL,
    on_lat REAL,
    on_lon REAL,
    duration_hours REAL NOT NULL,
    implied_speed_knots REAL,
    distance_km REAL,
    intentional_disabling INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS encounter_events (
    id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL,
    vessel_id TEXT NOT NULL,
    mmsi TEXT,
    vessel_name TEXT,
    flag TEXT,
    vessel_type TEXT,
    encountered_vessel_id TEXT,
    encountered_mmsi TEXT,
    encountered_name TEXT,
    encountered_flag TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    median_distance_km REAL,
    median_speed_knots REAL,
    encounter_type TEXT,
    potential_risk INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sar_cells (
    region TEXT NOT NULL,
    day TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    detections INTEGER NOT NULL,
    vessel_ids INTEGER NOT NULL,
    PRIMARY KEY (region, day, lat, lon)
);

CREATE TABLE IF NOT EXISTS positions (
    mmsi TEXT NOT NULL,
    ts TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    sog REAL,
    PRIMARY KEY (mmsi, ts)
);
"""

_GAP_UPSERT = """
INSERT INTO gap_events (
    id, region, vessel_id, mmsi, vessel_name, flag, vessel_type, start_ts, end_ts,
    off_lat, off_lon, on_lat, on_lon, duration_hours, implied_speed_knots, distance_km,
    intentional_disabling
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    region = excluded.region,
    vessel_id = excluded.vessel_id,
    mmsi = excluded.mmsi,
    vessel_name = excluded.vessel_name,
    flag = excluded.flag,
    vessel_type = excluded.vessel_type,
    start_ts = excluded.start_ts,
    end_ts = excluded.end_ts,
    off_lat = excluded.off_lat,
    off_lon = excluded.off_lon,
    on_lat = excluded.on_lat,
    on_lon = excluded.on_lon,
    duration_hours = excluded.duration_hours,
    implied_speed_knots = excluded.implied_speed_knots,
    distance_km = excluded.distance_km,
    intentional_disabling = excluded.intentional_disabling
"""

_GAP_SELECT = """
SELECT id, vessel_id, mmsi, vessel_name, flag, vessel_type, start_ts, end_ts,
       off_lat, off_lon, on_lat, on_lon, duration_hours, implied_speed_knots,
       distance_km, intentional_disabling
FROM gap_events
WHERE region = ? AND start_ts >= ? AND start_ts < ?
ORDER BY start_ts
"""

_ENCOUNTER_UPSERT = """
INSERT INTO encounter_events (
    id, region, start_ts, end_ts, vessel_id, mmsi, vessel_name, flag, vessel_type,
    encountered_vessel_id, encountered_mmsi, encountered_name, encountered_flag,
    lat, lon, median_distance_km, median_speed_knots, encounter_type, potential_risk
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    region = excluded.region,
    start_ts = excluded.start_ts,
    end_ts = excluded.end_ts,
    vessel_id = excluded.vessel_id,
    mmsi = excluded.mmsi,
    vessel_name = excluded.vessel_name,
    flag = excluded.flag,
    vessel_type = excluded.vessel_type,
    encountered_vessel_id = excluded.encountered_vessel_id,
    encountered_mmsi = excluded.encountered_mmsi,
    encountered_name = excluded.encountered_name,
    encountered_flag = excluded.encountered_flag,
    lat = excluded.lat,
    lon = excluded.lon,
    median_distance_km = excluded.median_distance_km,
    median_speed_knots = excluded.median_speed_knots,
    encounter_type = excluded.encounter_type,
    potential_risk = excluded.potential_risk
"""

_ENCOUNTER_SELECT = """
SELECT id, start_ts, end_ts, vessel_id, mmsi, vessel_name, flag, vessel_type,
       encountered_vessel_id, encountered_mmsi, encountered_name, encountered_flag,
       lat, lon, median_distance_km, median_speed_knots, encounter_type, potential_risk
FROM encounter_events
WHERE region = ? AND start_ts >= ? AND start_ts < ?
ORDER BY start_ts
"""

_SAR_UPSERT = """
INSERT INTO sar_cells (region, day, lat, lon, detections, vessel_ids)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(region, day, lat, lon) DO UPDATE SET
    detections = excluded.detections,
    vessel_ids = excluded.vessel_ids
"""

_SAR_SELECT = """
SELECT day, lat, lon, detections, vessel_ids
FROM sar_cells
WHERE region = ? AND day >= ? AND day <= ?
ORDER BY day, lat, lon
"""

_POSITION_UPSERT = """
INSERT INTO positions (mmsi, ts, lat, lon, sog)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(mmsi, ts) DO UPDATE SET
    lat = excluded.lat,
    lon = excluded.lon,
    sog = excluded.sog
"""

_DARK_HOURS = """
SELECT substr(start_ts, 1, 10) AS day,
       SUM(duration_hours) AS dark_hours,
       COUNT(*) AS gap_count
FROM gap_events
WHERE region = ?
GROUP BY day
ORDER BY day
"""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _ts(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_ts(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _day_after(value: date) -> str:
    return (value + timedelta(days=1)).isoformat()


class SqliteRepo:
    """SQLite-backed repository; the default for local development and tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            resolved = Path(path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(resolved)
        else:
            self._path = path
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA)

    def upsert_gaps(self, region: str, gaps: Sequence[GapEvent]) -> int:
        params = [
            (
                gap.id,
                region,
                gap.vessel_id,
                gap.mmsi,
                gap.vessel_name,
                gap.flag,
                gap.vessel_type,
                _ts(gap.start),
                _ts(gap.end),
                gap.off_lat,
                gap.off_lon,
                gap.on_lat,
                gap.on_lon,
                gap.duration_hours,
                gap.implied_speed_knots,
                gap.distance_km,
                int(gap.intentional_disabling),
            )
            for gap in gaps
        ]
        with self._conn:
            self._conn.executemany(_GAP_UPSERT, params)
        return len(params)

    def upsert_encounters(self, region: str, encounters: Sequence[EncounterEvent]) -> int:
        params = [
            (
                encounter.id,
                region,
                _ts(encounter.start),
                _ts(encounter.end),
                encounter.vessel_id,
                encounter.mmsi,
                encounter.vessel_name,
                encounter.flag,
                encounter.vessel_type,
                encounter.encountered_vessel_id,
                encounter.encountered_mmsi,
                encounter.encountered_name,
                encounter.encountered_flag,
                encounter.lat,
                encounter.lon,
                encounter.median_distance_km,
                encounter.median_speed_knots,
                encounter.encounter_type,
                int(encounter.potential_risk),
            )
            for encounter in encounters
        ]
        with self._conn:
            self._conn.executemany(_ENCOUNTER_UPSERT, params)
        return len(params)

    def upsert_sar_cells(self, region: str, cells: Sequence[SarCellRow]) -> int:
        params = [
            (region, cell.day.isoformat(), cell.lat, cell.lon, cell.detections, cell.vessel_ids)
            for cell in cells
        ]
        with self._conn:
            self._conn.executemany(_SAR_UPSERT, params)
        return len(params)

    def upsert_positions(self, positions: Sequence[PositionRow]) -> int:
        params = [
            (position.mmsi, _ts(position.ts), position.lat, position.lon, position.sog)
            for position in positions
        ]
        with self._conn:
            self._conn.executemany(_POSITION_UPSERT, params)
        return len(params)

    def gaps_in(self, region: str, start: date, end: date) -> list[GapEvent]:
        rows = self._conn.execute(_GAP_SELECT, (region, start.isoformat(), _day_after(end))).fetchall()
        return [
            GapEvent(
                id=row[0],
                vessel_id=row[1],
                mmsi=row[2],
                vessel_name=row[3],
                flag=row[4],
                vessel_type=row[5],
                start=_parse_ts(row[6]),
                end=_parse_ts(row[7]),
                off_lat=row[8],
                off_lon=row[9],
                on_lat=row[10],
                on_lon=row[11],
                duration_hours=row[12],
                implied_speed_knots=row[13],
                distance_km=row[14],
                intentional_disabling=bool(row[15]),
            )
            for row in rows
        ]

    def encounters_in(self, region: str, start: date, end: date) -> list[EncounterEvent]:
        rows = self._conn.execute(_ENCOUNTER_SELECT, (region, start.isoformat(), _day_after(end))).fetchall()
        return [
            EncounterEvent(
                id=row[0],
                start=_parse_ts(row[1]),
                end=_parse_ts(row[2]),
                vessel_id=row[3],
                mmsi=row[4],
                vessel_name=row[5],
                flag=row[6],
                vessel_type=row[7],
                encountered_vessel_id=row[8],
                encountered_mmsi=row[9],
                encountered_name=row[10],
                encountered_flag=row[11],
                lat=row[12],
                lon=row[13],
                median_distance_km=row[14],
                median_speed_knots=row[15],
                encounter_type=row[16],
                potential_risk=bool(row[17]),
            )
            for row in rows
        ]

    def sar_cells_in(self, region: str, start: date, end: date) -> list[SarCellRow]:
        rows = self._conn.execute(_SAR_SELECT, (region, start.isoformat(), end.isoformat())).fetchall()
        return [
            SarCellRow(
                day=date.fromisoformat(row[0]),
                lat=row[1],
                lon=row[2],
                detections=row[3],
                vessel_ids=row[4],
            )
            for row in rows
        ]

    def dark_hours_daily(self, region: str) -> list[DarkHoursDay]:
        rows = self._conn.execute(_DARK_HOURS, (region,)).fetchall()
        return [
            DarkHoursDay(
                day=date.fromisoformat(row[0]),
                dark_hours=float(row[1]),
                gap_count=int(row[2]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
