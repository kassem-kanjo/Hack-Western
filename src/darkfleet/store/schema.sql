-- Dark Fleet TimescaleDB schema. Safe to run on every startup: everything is IF NOT EXISTS.
--
-- Timescale hypertables require every unique index (including the primary key) to contain
-- the partitioning column, so primary keys put the time column first. Upserts in
-- timescale_repo.py use the same conflict targets.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ``positions`` holds both GFW position rows and raw AIS pings (issue #72). The AIS-only
-- columns stay nullable so the older GFW ingest keeps writing the same five columns.
CREATE TABLE IF NOT EXISTS positions (
    ts timestamptz NOT NULL,
    mmsi text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    sog real,
    cog real,
    heading integer,
    nav_status integer,
    PRIMARY KEY (ts, mmsi)
);
SELECT create_hypertable('positions', 'ts', if_not_exists => TRUE);

-- Databases created before #72 lack the AIS columns; add them in place so a deployed
-- instance upgrades on the next startup without losing pings.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS cog real;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS heading integer;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS nav_status integer;

-- The live AIS sink (scripts/tiger_sink.py) tags every ping with the recorder folder it
-- came from, so the area strip can ask Tiger for one region's minutes. GFW position rows
-- and pre-#208 AIS rows keep NULL here.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS region text;

-- Hourly message counts over every position row: GFW position reports and raw AIS pings
-- share the table, so this is the recorder's own activity, not a gap series. No DISTINCT
-- (Timescale continuous aggregates do not allow it); count(*) is already one row per ping.
CREATE MATERIALIZED VIEW IF NOT EXISTS ais_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS hour,
       count(*) AS msgs
FROM positions
GROUP BY time_bucket('1 hour', ts)
WITH NO DATA;

SELECT remove_continuous_aggregate_policy('ais_hourly', if_exists => TRUE);

SELECT add_continuous_aggregate_policy(
    'ais_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Per-region message counts at minute resolution: the area strip's sparkline and
-- messages-per-minute read this instead of re-parsing the recorder's hour files.
CREATE MATERIALIZED VIEW IF NOT EXISTS ais_minutely
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', ts) AS minute,
       region,
       count(*) AS msgs
FROM positions
GROUP BY time_bucket('1 minute', ts), region
WITH NO DATA;

-- Real-time aggregation: the newest minutes are not materialized yet (the policy runs every
-- 60 s with a 1-minute end offset), so without this the live strip read 0 for the current
-- minute. Real-time mode unions the raw rows past the watermark.
ALTER MATERIALIZED VIEW ais_minutely SET (timescaledb.materialized_only = false);

SELECT remove_continuous_aggregate_policy('ais_minutely', if_exists => TRUE);

SELECT add_continuous_aggregate_policy(
    'ais_minutely',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);

-- Latest static/voyage data per vessel (AIS messages 5 and 24). One row per MMSI.
CREATE TABLE IF NOT EXISTS vessels_static (
    mmsi text PRIMARY KEY,
    imo integer,
    name text,
    callsign text,
    ship_type integer,
    length_m double precision,
    destination text,
    last_seen timestamptz
);

-- Recorder presence per UTC minute: the difference between a vessel going dark and our
-- receiver going dark. Not a hypertable: it is one row per minute and read whole.
CREATE TABLE IF NOT EXISTS coverage_minutes (
    minute timestamptz PRIMARY KEY,
    n_msgs integer NOT NULL
);

CREATE TABLE IF NOT EXISTS gap_events (
    id text NOT NULL,
    region text NOT NULL,
    vessel_id text NOT NULL,
    mmsi text,
    vessel_name text,
    flag text,
    vessel_type text,
    start_ts timestamptz NOT NULL,
    end_ts timestamptz NOT NULL,
    off_lat double precision,
    off_lon double precision,
    on_lat double precision,
    on_lon double precision,
    duration_hours double precision NOT NULL,
    implied_speed_knots double precision,
    distance_km double precision,
    intentional_disabling boolean NOT NULL,
    PRIMARY KEY (start_ts, id)
);
SELECT create_hypertable('gap_events', 'start_ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS encounter_events (
    id text NOT NULL,
    region text NOT NULL,
    start_ts timestamptz NOT NULL,
    end_ts timestamptz NOT NULL,
    vessel_id text NOT NULL,
    mmsi text,
    vessel_name text,
    flag text,
    vessel_type text,
    encountered_vessel_id text,
    encountered_mmsi text,
    encountered_name text,
    encountered_flag text,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    median_distance_km double precision,
    median_speed_knots double precision,
    encounter_type text,
    potential_risk boolean NOT NULL,
    PRIMARY KEY (start_ts, id)
);
SELECT create_hypertable('encounter_events', 'start_ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS sar_cells (
    region text NOT NULL,
    day date NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    detections integer NOT NULL,
    vessel_ids integer NOT NULL,
    PRIMARY KEY (day, region, lat, lon)
);
SELECT create_hypertable('sar_cells', 'day', if_not_exists => TRUE);

-- Per-day unseen-fleet series (issue #88): every radar detection, the subset also seen
-- broadcasting AIS, and the remainder. One row per region/day; no vessel identity.
CREATE TABLE IF NOT EXISTS sar_daily (
    day date NOT NULL,
    region text NOT NULL,
    seen integer NOT NULL,
    broadcasting integer NOT NULL,
    dark integer NOT NULL,
    PRIMARY KEY (day, region)
);
SELECT create_hypertable('sar_daily', 'day', if_not_exists => TRUE);

-- Weekly roll-up for the fleet chart. A gap can straddle a week boundary, but summing
-- whole UTC days keeps the series comparable with the daily rows.
CREATE MATERIALIZED VIEW IF NOT EXISTS sar_weekly
WITH (timescaledb.continuous) AS
SELECT time_bucket('7 days', day) AS week,
       region,
       sum(seen) AS seen,
       sum(broadcasting) AS broadcasting,
       sum(dark) AS dark
FROM sar_daily
GROUP BY time_bucket('7 days', day), region
WITH NO DATA;

SELECT remove_continuous_aggregate_policy('sar_weekly', if_exists => TRUE);

SELECT add_continuous_aggregate_policy(
    'sar_weekly',
    start_offset => INTERVAL '1 year',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- A gap is attributed to the UTC day it *starts* on; for timestamptz, time_bucket with a
-- fixed '1 day' width uses a midnight-UTC origin.
--
-- Earlier builds grouped this aggregate by region under the names dark_hours/gap_count.
-- Recreate it when it has the old shape so the definition below is the one in use; an
-- aggregate that already matches is left untouched, so this block stays idempotent.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'dark_hours_daily'
          AND column_name = 'dark_hours'
    ) THEN
        DROP MATERIALIZED VIEW dark_hours_daily;
    END IF;
END
$$;

CREATE MATERIALIZED VIEW IF NOT EXISTS dark_hours_daily
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', start_ts) AS day,
       sum(duration_hours) AS hours,
       count(*) AS gaps
FROM gap_events
GROUP BY time_bucket('1 day', start_ts)
WITH NO DATA;

-- Replace the policy (if any) so its offsets always match this schema revision.
SELECT remove_continuous_aggregate_policy('dark_hours_daily', if_exists => TRUE);

SELECT add_continuous_aggregate_policy(
    'dark_hours_daily',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
