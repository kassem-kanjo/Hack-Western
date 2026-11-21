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

from darkfleet.core.tracks import CoverageMinute, Ping, VesselInfo
from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow
from darkfleet.store.repository import (
    AisHourBucket,
    DarkHoursBucket,
    DarkHoursDay,
    PositionRow,
    SarDailyRow,
    SarWeekBucket,
)

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

CREATE TABLE IF NOT EXISTS sar_daily (
    region TEXT NOT NULL,
    day TEXT NOT NULL,
    seen INTEGER NOT NULL,
    broadcasting INTEGER NOT NULL,
    dark INTEGER NOT NULL,
    PRIMARY KEY (region, day)
);

CREATE TABLE IF NOT EXISTS positions (
    mmsi TEXT NOT NULL,
    ts TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    sog REAL,
    cog REAL,
    heading INTEGER,
    nav_status INTEGER,
    PRIMARY KEY (mmsi, ts)
);

CREATE TABLE IF NOT EXISTS vessels_static (
    mmsi TEXT PRIMARY KEY,
    imo INTEGER,
    name TEXT,
    callsign TEXT,
    ship_type INTEGER,
    length_m REAL,
    destination TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS coverage_minutes (
    minute TEXT PRIMARY KEY,
    n_msgs INTEGER NOT NULL
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

_SAR_DAILY_UPSERT = """
INSERT INTO sar_daily (region, day, seen, broadcasting, dark)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(region, day) DO UPDATE SET
    seen = excluded.seen,
    broadcasting = excluded.broadcasting,
    dark = excluded.dark
"""

_POSITION_UPSERT = """
INSERT INTO positions (mmsi, ts, lat, lon, sog)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(mmsi, ts) DO UPDATE SET
    lat = excluded.lat,
    lon = excluded.lon,
    sog = excluded.sog
"""

# Raw AIS pings share the positions table with GFW rows; the AIS-only columns stay NULL for
# GFW writes and the GFW-only columns are absent on a ping, so one upsert per writer.
_PING_UPSERT = """
INSERT INTO positions (mmsi, ts, lat, lon, sog, cog, heading, nav_status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(mmsi, ts) DO UPDATE SET
    lat = excluded.lat,
    lon = excluded.lon,
    sog = excluded.sog,
    cog = excluded.cog,
    heading = excluded.heading,
    nav_status = excluded.nav_status
"""

_PING_SELECT = """
SELECT mmsi, ts, lat, lon, sog, cog, heading, nav_status
FROM positions
WHERE ts >= ? AND ts <= ?
ORDER BY ts, mmsi
"""

_VESSEL_UPSERT = """
INSERT INTO vessels_static (mmsi, imo, name, callsign, ship_type, length_m, destination, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(mmsi) DO UPDATE SET
    imo = excluded.imo,
    name = excluded.name,
    callsign = excluded.callsign,
    ship_type = excluded.ship_type,
    length_m = excluded.length_m,
    destination = excluded.destination,
    last_seen = excluded.last_seen
"""

_VESSEL_SELECT = """
SELECT mmsi, imo, name, callsign, ship_type, length_m, destination, last_seen
FROM vessels_static
ORDER BY mmsi
"""

_COVERAGE_UPSERT = """
INSERT INTO coverage_minutes (minute, n_msgs)
VALUES (?, ?)
ON CONFLICT(minute) DO UPDATE SET n_msgs = excluded.n_msgs
"""

_COVERAGE_SELECT = """
SELECT minute, n_msgs
FROM coverage_minutes
WHERE minute >= ? AND minute <= ?
ORDER BY minute
"""

_DARK_HOURS_BY_REGION = """
SELECT substr(start_ts, 1, 10) AS day,
       SUM(duration_hours) AS dark_hours,
       COUNT(*) AS gap_count
FROM gap_events
WHERE region = ?
GROUP BY day
ORDER BY day
"""

# Gap start days in [start, end], fleet-wide. start_ts is stored as UTC ISO-8601, so the
# half-open string range on the leading timestamp prefix is an index-friendly day filter.
_DARK_HOURS_RANGE = """
SELECT date(start_ts) AS day,
       SUM(duration_hours) AS hours,
       COUNT(*) AS gaps
FROM gap_events
WHERE start_ts >= ? AND start_ts < ?
GROUP BY day
ORDER BY day
"""

# Equivalent of the Timescale weekly aggregate: 7-day buckets aligned to the same Monday
# origin (2000-01-03), one row per region and bucket that had radar days.
_SAR_WEEKLY_RANGE = """
SELECT date(day, 'weekday 0', '-6 days') AS week,
       region,
       SUM(seen) AS seen,
       SUM(broadcasting) AS broadcasting,
       SUM(dark) AS dark
FROM sar_daily
WHERE day >= ? AND day < ?
GROUP BY week, region
ORDER BY week, region
"""

# Equivalent of the Timescale hourly aggregate: timestamps are stored as UTC ISO-8601, so
# the leading 13 characters are the UTC hour and group cleanly.
_AIS_HOURLY_RANGE = """
SELECT substr(ts, 1, 13) AS hour,
       COUNT(*) AS msgs
FROM positions
WHERE ts >= ? AND ts < ?
GROUP BY hour
ORDER BY hour
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


def _migrate_positions(conn: sqlite3.Connection) -> None:
    """Add the AIS columns to a positions table created before issue #72.

    ``CREATE TABLE IF NOT EXISTS`` cannot widen an existing table, and the local SQLite cache
    survives code changes, so the upgrade has to happen in place or every insert fails.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for column, declaration in (("cog", "REAL"), ("heading", "INTEGER"), ("nav_status", "INTEGER")):
        if column not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {column} {declaration}")


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
        with self._conn:
            _migrate_positions(self._conn)

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

    def upsert_sar_daily(self, rows: Sequence[SarDailyRow]) -> int:
        params = [(row.region, row.day.isoformat(), row.seen, row.broadcasting, row.dark) for row in rows]
        with self._conn:
            self._conn.executemany(_SAR_DAILY_UPSERT, params)
        return len(params)

    def upsert_positions(self, positions: Sequence[PositionRow]) -> int:
        params = [
            (position.mmsi, _ts(position.ts), position.lat, position.lon, position.sog)
            for position in positions
        ]
        with self._conn:
            self._conn.executemany(_POSITION_UPSERT, params)
        return len(params)

    def add_pings(self, pings: Sequence[Ping]) -> int:
        params = [
            (
                str(ping.mmsi),
                _ts(ping.t),
                ping.lat,
                ping.lon,
                ping.sog,
                ping.cog,
                ping.heading,
                ping.nav_status,
            )
            for ping in pings
        ]
        with self._conn:
            self._conn.executemany(_PING_UPSERT, params)
        return len(params)

    def pings_between(self, start: datetime, end: datetime) -> list[Ping]:
        rows = self._conn.execute(_PING_SELECT, (_ts(start), _ts(end))).fetchall()
        return [
            Ping(
                mmsi=int(row[0]),
                t=_parse_ts(row[1]),
                lat=row[2],
                lon=row[3],
                sog=row[4],
                cog=row[5],
                heading=row[6],
                nav_status=row[7],
            )
            for row in rows
        ]

    def upsert_vessels(self, vessels: Sequence[VesselInfo]) -> int:
        params = [
            (
                str(vessel.mmsi),
                vessel.imo,
                vessel.name,
                vessel.callsign,
                vessel.ship_type,
                vessel.length_m,
                vessel.destination,
                None if vessel.last_seen is None else _ts(vessel.last_seen),
            )
            for vessel in vessels
        ]
        with self._conn:
            self._conn.executemany(_VESSEL_UPSERT, params)
        return len(params)

    def vessels(self) -> list[VesselInfo]:
        rows = self._conn.execute(_VESSEL_SELECT).fetchall()
        return [
            VesselInfo(
                mmsi=int(row[0]),
                imo=row[1],
                name=row[2],
                callsign=row[3],
                ship_type=row[4],
                length_m=row[5],
                destination=row[6],
                last_seen=None if row[7] is None else _parse_ts(row[7]),
            )
            for row in rows
        ]

    def set_coverage(self, minutes: Sequence[CoverageMinute]) -> int:
        params = [(_ts(item.minute), item.n_msgs) for item in minutes]
        with self._conn:
            self._conn.executemany(_COVERAGE_UPSERT, params)
        return len(params)

    def coverage_between(self, start: datetime, end: datetime) -> list[CoverageMinute]:
        rows = self._conn.execute(_COVERAGE_SELECT, (_ts(start), _ts(end))).fetchall()
        return [CoverageMinute(minute=_parse_ts(row[0]), n_msgs=int(row[1])) for row in rows]

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

    def sar_weekly(self, start: date, end: date) -> list[SarWeekBucket]:
        """Weekly radar series computed from ``sar_daily``; matches the Timescale buckets."""
        rows = self._conn.execute(_SAR_WEEKLY_RANGE, (start.isoformat(), _day_after(end))).fetchall()
        return [
            SarWeekBucket(
                week=date.fromisoformat(row[0]),
                region=row[1],
                seen=int(row[2]),
                broadcasting=int(row[3]),
                dark=int(row[4]),
            )
            for row in rows
        ]

    def ais_hourly(self, start: datetime, end: datetime) -> list[AisHourBucket]:
        """Hourly position counts grouped from ``positions``; matches the Timescale buckets."""
        rows = self._conn.execute(_AIS_HOURLY_RANGE, (_ts(start), _ts(end))).fetchall()
        return [
            AisHourBucket(
                hour=_as_utc(datetime.fromisoformat(f"{row[0]}:00:00")),
                msgs=int(row[1]),
            )
            for row in rows
        ]

    def dark_hours_daily(
        self,
        start: date | str,
        end: date | None = None,
        *,
        refresh: bool = False,
    ) -> list[DarkHoursDay] | list[DarkHoursBucket]:
        """Daily dark hours. ``dark_hours_daily(region)`` keeps the region-scoped shape;
        ``dark_hours_daily(start, end)`` returns fleet-wide ``{day, hours, gaps}`` buckets.

        SQLite answers from the rows themselves, so ``refresh`` is accepted and ignored.
        """
        if isinstance(start, str):
            if end is not None:
                raise TypeError("dark_hours_daily(region) takes no end date")
            rows = self._conn.execute(_DARK_HOURS_BY_REGION, (start,)).fetchall()
            return [
                DarkHoursDay(
                    day=date.fromisoformat(row[0]),
                    dark_hours=float(row[1]),
                    gap_count=int(row[2]),
                )
                for row in rows
            ]

        if end is None:
            raise TypeError("dark_hours_daily(start, end) needs both dates")
        rows = self._conn.execute(_DARK_HOURS_RANGE, (start.isoformat(), _day_after(end))).fetchall()
        return [
            DarkHoursBucket(
                day=date.fromisoformat(row[0]),
                hours=float(row[1]),
                gaps=int(row[2]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
