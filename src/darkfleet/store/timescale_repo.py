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

from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow
from darkfleet.store.repository import DarkHoursDay, PositionRow

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

_POSITION_UPSERT = """
INSERT INTO positions (ts, mmsi, lat, lon, sog)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (ts, mmsi) DO UPDATE SET
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    sog = EXCLUDED.sog
"""

_DARK_HOURS = """
SELECT (day AT TIME ZONE 'UTC')::date AS day, dark_hours, gap_count
FROM dark_hours_daily
WHERE region = %s
ORDER BY day
"""

_REFRESH = "CALL refresh_continuous_aggregate('dark_hours_daily', NULL, NULL)"


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

    def __init__(self, url: str, *, schema: str | None = None) -> None:
        connect_kwargs: dict[str, object] = {"autocommit": True}
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

    def dark_hours_daily(self, region: str) -> list[DarkHoursDay]:
        rows = self._conn.execute(_DARK_HOURS, (region,)).fetchall()
        return [
            DarkHoursDay(
                day=row[0],
                dark_hours=float(row[1]),
                gap_count=int(row[2]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
