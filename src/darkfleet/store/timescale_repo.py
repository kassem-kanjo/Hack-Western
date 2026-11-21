"""TimescaleDB/PostgreSQL implementation of the repository protocol (psycopg 3).

Primary keys lead with the partitioning column, as Timescale requires, and the upsert
conflict targets mirror them. ``dark_hours_daily`` is served by a continuous aggregate,
refreshed after every gap batch.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from importlib.resources import files

import psycopg

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

__all__ = ["TimescaleRepo"]

_GAP_UPSERT = """
INSERT INTO gap_events (
    id, region, vessel_id, mmsi, vessel_name, flag, vessel_type, start_ts, end_ts,
    off_lat, off_lon, on_lat, on_lon, duration_hours, implied_speed_knots, distance_km,
    intentional_disabling
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (start_ts, id) DO UPDATE SET
    region = EXCLUDED.region,
    vessel_id = EXCLUDED.vessel_id,
    mmsi = EXCLUDED.mmsi,
    vessel_name = EXCLUDED.vessel_name,
    flag = EXCLUDED.flag,
    vessel_type = EXCLUDED.vessel_type,
    end_ts = EXCLUDED.end_ts,
    off_lat = EXCLUDED.off_lat,
    off_lon = EXCLUDED.off_lon,
    on_lat = EXCLUDED.on_lat,
    on_lon = EXCLUDED.on_lon,
    duration_hours = EXCLUDED.duration_hours,
    implied_speed_knots = EXCLUDED.implied_speed_knots,
    distance_km = EXCLUDED.distance_km,
    intentional_disabling = EXCLUDED.intentional_disabling
"""

_GAP_SELECT = """
SELECT id, vessel_id, mmsi, vessel_name, flag, vessel_type, start_ts, end_ts,
       off_lat, off_lon, on_lat, on_lon, duration_hours, implied_speed_knots,
       distance_km, intentional_disabling
FROM gap_events
WHERE region = %s AND start_ts >= %s AND start_ts < %s
ORDER BY start_ts
"""

_ENCOUNTER_UPSERT = """
INSERT INTO encounter_events (
    id, region, start_ts, end_ts, vessel_id, mmsi, vessel_name, flag, vessel_type,
    encountered_vessel_id, encountered_mmsi, encountered_name, encountered_flag,
    lat, lon, median_distance_km, median_speed_knots, encounter_type, potential_risk
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (start_ts, id) DO UPDATE SET
    region = EXCLUDED.region,
    end_ts = EXCLUDED.end_ts,
    vessel_id = EXCLUDED.vessel_id,
    mmsi = EXCLUDED.mmsi,
    vessel_name = EXCLUDED.vessel_name,
    flag = EXCLUDED.flag,
    vessel_type = EXCLUDED.vessel_type,
    encountered_vessel_id = EXCLUDED.encountered_vessel_id,
    encountered_mmsi = EXCLUDED.encountered_mmsi,
    encountered_name = EXCLUDED.encountered_name,
    encountered_flag = EXCLUDED.encountered_flag,
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    median_distance_km = EXCLUDED.median_distance_km,
    median_speed_knots = EXCLUDED.median_speed_knots,
    encounter_type = EXCLUDED.encounter_type,
    potential_risk = EXCLUDED.potential_risk
"""

_ENCOUNTER_SELECT = """
SELECT id, start_ts, end_ts, vessel_id, mmsi, vessel_name, flag, vessel_type,
       encountered_vessel_id, encountered_mmsi, encountered_name, encountered_flag,
       lat, lon, median_distance_km, median_speed_knots, encounter_type, potential_risk
FROM encounter_events
WHERE region = %s AND start_ts >= %s AND start_ts < %s
ORDER BY start_ts
"""

_SAR_UPSERT = """
INSERT INTO sar_cells (region, day, lat, lon, detections, vessel_ids)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (day, region, lat, lon) DO UPDATE SET
    detections = EXCLUDED.detections,
    vessel_ids = EXCLUDED.vessel_ids
"""

_SAR_SELECT = """
SELECT day, lat, lon, detections, vessel_ids
FROM sar_cells
WHERE region = %s AND day >= %s AND day <= %s
ORDER BY day, lat, lon
"""

_SAR_DAILY_UPSERT = """
INSERT INTO sar_daily (day, region, seen, broadcasting, dark)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (day, region) DO UPDATE SET
    seen = EXCLUDED.seen,
    broadcasting = EXCLUDED.broadcasting,
    dark = EXCLUDED.dark
"""

_POSITION_UPSERT = """
INSERT INTO positions (ts, mmsi, lat, lon, sog)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (ts, mmsi) DO UPDATE SET
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    sog = EXCLUDED.sog
"""

_PING_UPSERT = """
INSERT INTO positions (ts, mmsi, lat, lon, sog, cog, heading, nav_status)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ts, mmsi) DO UPDATE SET
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    sog = EXCLUDED.sog,
    cog = EXCLUDED.cog,
    heading = EXCLUDED.heading,
    nav_status = EXCLUDED.nav_status
"""

_PING_SELECT = """
SELECT mmsi, ts, lat, lon, sog, cog, heading, nav_status
FROM positions
WHERE ts >= %s AND ts <= %s
ORDER BY ts, mmsi
"""

# The live sink tags each ping with its recorder region. DO NOTHING (not DO UPDATE) is
# deliberate: replaying a file after a crash must not rewrite rows a concurrent reader saw.
_REGION_PING_INSERT = """
INSERT INTO positions (ts, mmsi, lat, lon, sog, cog, heading, nav_status, region)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ts, mmsi) DO NOTHING
"""

_AIS_MINUTELY_RANGE = """
SELECT minute, msgs
FROM ais_minutely
WHERE region = %s AND minute >= %s AND minute < %s
ORDER BY minute
"""

_AIS_MINUTELY_REFRESH_RANGE = "CALL refresh_continuous_aggregate('ais_minutely', %s, %s)"

_VESSEL_UPSERT = """
INSERT INTO vessels_static (mmsi, imo, name, callsign, ship_type, length_m, destination, last_seen)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (mmsi) DO UPDATE SET
    imo = EXCLUDED.imo,
    name = EXCLUDED.name,
    callsign = EXCLUDED.callsign,
    ship_type = EXCLUDED.ship_type,
    length_m = EXCLUDED.length_m,
    destination = EXCLUDED.destination,
    last_seen = EXCLUDED.last_seen
"""

_VESSEL_SELECT = """
SELECT mmsi, imo, name, callsign, ship_type, length_m, destination, last_seen
FROM vessels_static
ORDER BY mmsi
"""

_COVERAGE_UPSERT = """
INSERT INTO coverage_minutes (minute, n_msgs)
VALUES (%s, %s)
ON CONFLICT (minute) DO UPDATE SET n_msgs = EXCLUDED.n_msgs
"""

_COVERAGE_SELECT = """
SELECT minute, n_msgs
FROM coverage_minutes
WHERE minute >= %s AND minute <= %s
ORDER BY minute
"""

_DARK_HOURS_BY_REGION = """
-- The region-scoped shape predates the fleet-wide continuous aggregate; compute it from
-- the hypertable so existing callers keep working after the aggregate changed shape.
SELECT (start_ts AT TIME ZONE 'UTC')::date AS day,
       sum(duration_hours) AS dark_hours,
       count(*) AS gap_count
FROM gap_events
WHERE region = %s
GROUP BY day
ORDER BY day
"""

# The continuous aggregate holds fleet-wide buckets: one row per UTC day that had a gap.
_DARK_HOURS_RANGE = """
SELECT (day AT TIME ZONE 'UTC')::date AS day, hours, gaps
FROM dark_hours_daily
WHERE day >= %s AND day < %s
ORDER BY day
"""

_REFRESH_RANGE = "CALL refresh_continuous_aggregate('dark_hours_daily', %s, %s)"

_REFRESH = "CALL refresh_continuous_aggregate('dark_hours_daily', NULL, NULL)"

_SAR_WEEKLY_REFRESH = "CALL refresh_continuous_aggregate('sar_weekly', NULL, NULL)"

# The weekly aggregate buckets whole UTC days from the Timescale 7-day origin (a Monday),
# so the window is passed as timestamps and the view expands it to its own boundaries.
_SAR_WEEKLY_RANGE = """
SELECT week::date AS week, region, seen, broadcasting, dark
FROM sar_weekly
WHERE week >= %s AND week < %s
ORDER BY week, region
"""

_SAR_WEEKLY_REFRESH_RANGE = "CALL refresh_continuous_aggregate('sar_weekly', %s, %s)"

# Hourly position counts. The continuous aggregate is refreshed for the requested window
# before every read: refresh_continuous_aggregate cannot run inside a transaction, and
# this connection is autocommit.
_AIS_HOURLY_RANGE = """
SELECT hour, msgs
FROM ais_hourly
WHERE hour >= %s AND hour < %s
ORDER BY hour
"""

_AIS_HOURLY_REFRESH_RANGE = "CALL refresh_continuous_aggregate('ais_hourly', %s, %s)"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _start_of(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _day_after(value: date) -> datetime:
    return _start_of(value + timedelta(days=1))


class TimescaleRepo:
    """TimescaleDB-backed repository for production."""

    def __init__(
        self,
        url: str,
        *,
        schema: str | None = None,
        connect_timeout: int | float | None = None,
    ) -> None:
        connect_kwargs: dict[str, object] = {"autocommit": True}
        if connect_timeout is not None:
            connect_kwargs["connect_timeout"] = connect_timeout
        if schema is not None:
            # Keep public on the path: the Timescale functions (create_hypertable, time_bucket) live there.
            connect_kwargs["options"] = f"-c search_path={schema},public"
        self._conn = psycopg.connect(url, **connect_kwargs)
        schema_sql = (files("darkfleet.store") / "schema.sql").read_text(encoding="utf-8")
        self._conn.execute(schema_sql)

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
                _as_utc(gap.start),
                _as_utc(gap.end),
                gap.off_lat,
                gap.off_lon,
                gap.on_lat,
                gap.on_lon,
                gap.duration_hours,
                gap.implied_speed_knots,
                gap.distance_km,
                gap.intentional_disabling,
            )
            for gap in gaps
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_GAP_UPSERT, params)
        self._conn.execute(_REFRESH)
        return len(params)

    def upsert_encounters(self, region: str, encounters: Sequence[EncounterEvent]) -> int:
        params = [
            (
                encounter.id,
                region,
                _as_utc(encounter.start),
                _as_utc(encounter.end),
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
                encounter.potential_risk,
            )
            for encounter in encounters
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_ENCOUNTER_UPSERT, params)
        return len(params)

    def upsert_sar_cells(self, region: str, cells: Sequence[SarCellRow]) -> int:
        params = [(region, cell.day, cell.lat, cell.lon, cell.detections, cell.vessel_ids) for cell in cells]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_SAR_UPSERT, params)
        return len(params)

    def upsert_sar_daily(self, rows: Sequence[SarDailyRow]) -> int:
        """Write the daily series and refresh the weekly continuous aggregate."""
        params = [(row.day, row.region, row.seen, row.broadcasting, row.dark) for row in rows]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_SAR_DAILY_UPSERT, params)
        self._conn.execute(_SAR_WEEKLY_REFRESH)
        return len(params)

    def upsert_positions(self, positions: Sequence[PositionRow]) -> int:
        params = [
            (
                _as_utc(position.ts),
                position.mmsi,
                position.lat,
                position.lon,
                position.sog,
            )
            for position in positions
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_POSITION_UPSERT, params)
        return len(params)

    def add_pings(self, pings: Sequence[Ping]) -> int:
        params = [
            (
                _as_utc(ping.t),
                str(ping.mmsi),
                ping.lat,
                ping.lon,
                ping.sog,
                ping.cog,
                ping.heading,
                ping.nav_status,
            )
            for ping in pings
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_PING_UPSERT, params)
        return len(params)

    def add_region_pings(self, region: str, pings: Sequence[Ping]) -> int:
        """Insert pings tagged with the recorder region, skipping anything already stored.

        The caller owns batching (the sink sends 5,000 at a time); one call is one
        transaction. Returns the number of rows submitted, not the number newly inserted:
        the sink's offsets count pings it has *sent*, and a duplicate is already accounted
        for by the natural ``(ts, mmsi)`` key.
        """
        params = [
            (
                _as_utc(ping.t),
                str(ping.mmsi),
                ping.lat,
                ping.lon,
                ping.sog,
                ping.cog,
                ping.heading,
                ping.nav_status,
                region,
            )
            for ping in pings
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_REGION_PING_INSERT, params)
        return len(params)

    def pings_between(self, start: datetime, end: datetime) -> list[Ping]:
        rows = self._conn.execute(_PING_SELECT, (_as_utc(start), _as_utc(end))).fetchall()
        return [
            Ping(
                mmsi=int(row[0]),
                t=_as_utc(row[1]),
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
                None if vessel.last_seen is None else _as_utc(vessel.last_seen),
            )
            for vessel in vessels
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_VESSEL_UPSERT, params)
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
                last_seen=None if row[7] is None else _as_utc(row[7]),
            )
            for row in rows
        ]

    def set_coverage(self, minutes: Sequence[CoverageMinute]) -> int:
        params = [(_as_utc(item.minute), item.n_msgs) for item in minutes]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(_COVERAGE_UPSERT, params)
        return len(params)

    def coverage_between(self, start: datetime, end: datetime) -> list[CoverageMinute]:
        rows = self._conn.execute(_COVERAGE_SELECT, (_as_utc(start), _as_utc(end))).fetchall()
        return [CoverageMinute(minute=_as_utc(row[0]), n_msgs=int(row[1])) for row in rows]

    def gaps_in(self, region: str, start: date, end: date) -> list[GapEvent]:
        rows = self._conn.execute(_GAP_SELECT, (region, _start_of(start), _day_after(end))).fetchall()
        return [
            GapEvent(
                id=row[0],
                vessel_id=row[1],
                mmsi=row[2],
                vessel_name=row[3],
                flag=row[4],
                vessel_type=row[5],
                start=row[6],
                end=row[7],
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
        rows = self._conn.execute(_ENCOUNTER_SELECT, (region, _start_of(start), _day_after(end))).fetchall()
        return [
            EncounterEvent(
                id=row[0],
                start=row[1],
                end=row[2],
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
        rows = self._conn.execute(_SAR_SELECT, (region, start, end)).fetchall()
        return [
            SarCellRow(
                day=row[0],
                lat=row[1],
                lon=row[2],
                detections=row[3],
                vessel_ids=row[4],
            )
            for row in rows
        ]

    def sar_weekly(self, start: date, end: date) -> list[SarWeekBucket]:
        """Weekly radar series. Materialises the window, then reads the aggregate."""
        # sar_weekly is bucketed on a date column, so its refresh window must be dates too
        # (Timescale rejects timestamptz arguments there).
        window_start = start
        window_end = end + timedelta(days=1)
        self._conn.execute(_SAR_WEEKLY_REFRESH_RANGE, (window_start, window_end))
        rows = self._conn.execute(_SAR_WEEKLY_RANGE, (window_start, window_end)).fetchall()
        return [
            SarWeekBucket(
                week=row[0],
                region=row[1],
                seen=int(row[2]),
                broadcasting=int(row[3]),
                dark=int(row[4]),
            )
            for row in rows
        ]

    def ais_hourly(self, start: datetime, end: datetime) -> list[AisHourBucket]:
        """Hourly position counts. Materialises the window, then reads the aggregate."""
        window_start = _as_utc(start)
        window_end = _as_utc(end)
        self._conn.execute(_AIS_HOURLY_REFRESH_RANGE, (window_start, window_end))
        rows = self._conn.execute(_AIS_HOURLY_RANGE, (window_start, window_end)).fetchall()
        return [AisHourBucket(hour=_as_utc(row[0]), msgs=int(row[1])) for row in rows]

    def minutely(self, region: str, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
        """Per-region message counts for minutes in ``[start, end)``, ordered by minute.

        Materialises the window first (the policy only covers the last three hours and a
        demo may replay an older recording), then reads ``ais_minutely``. Only minutes with
        at least one ping appear; callers fill the gaps with zero.
        """
        window_start = _as_utc(start)
        window_end = _as_utc(end)
        self._conn.execute(_AIS_MINUTELY_REFRESH_RANGE, (window_start, window_end))
        rows = self._conn.execute(_AIS_MINUTELY_RANGE, (region, window_start, window_end)).fetchall()
        return [(_as_utc(row[0]), int(row[1])) for row in rows]

    def dark_hours_daily(
        self,
        start: date | str,
        end: date | None = None,
        *,
        refresh: bool = False,
    ) -> list[DarkHoursDay] | list[DarkHoursBucket]:
        """Daily dark hours. ``dark_hours_daily(region)`` keeps the region-scoped shape;
        ``dark_hours_daily(start, end, refresh=True)`` reads the fleet-wide continuous
        aggregate after materialising the window.
        """
        if isinstance(start, str):
            if end is not None:
                raise TypeError("dark_hours_daily(region) takes no end date")
            rows = self._conn.execute(_DARK_HOURS_BY_REGION, (start,)).fetchall()
            return [
                DarkHoursDay(
                    day=row[0],
                    dark_hours=float(row[1]),
                    gap_count=int(row[2]),
                )
                for row in rows
            ]

        if end is None:
            raise TypeError("dark_hours_daily(start, end) needs both dates")
        start_ts = _start_of(start)
        end_ts = _day_after(end)
        if refresh:
            self._conn.execute(_REFRESH_RANGE, (start_ts, end_ts))
        rows = self._conn.execute(_DARK_HOURS_RANGE, (start_ts, end_ts)).fetchall()
        return [DarkHoursBucket(day=row[0], hours=float(row[1]), gaps=int(row[2])) for row in rows]

    def close(self) -> None:
        self._conn.close()
