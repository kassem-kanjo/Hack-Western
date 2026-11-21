-- Dark Fleet TimescaleDB schema. Safe to run on every startup: everything is IF NOT EXISTS.
--
-- Timescale hypertables require every unique index (including the primary key) to contain
-- the partitioning column, so primary keys put the time column first. Upserts in
-- timescale_repo.py use the same conflict targets.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS positions (
    ts timestamptz NOT NULL,
    mmsi text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    sog real,
    PRIMARY KEY (ts, mmsi)
);
SELECT create_hypertable('positions', 'ts', if_not_exists => TRUE);

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

-- A gap is attributed to the UTC day it *starts* on; for timestamptz, time_bucket with a
-- fixed '1 day' width uses a midnight-UTC origin.
CREATE MATERIALIZED VIEW IF NOT EXISTS dark_hours_daily
WITH (timescaledb.continuous) AS
SELECT region,
       time_bucket('1 day', start_ts) AS day,
       sum(duration_hours) AS dark_hours,
       count(*) AS gap_count
FROM gap_events
GROUP BY region, time_bucket('1 day', start_ts)
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'dark_hours_daily',
    start_offset => NULL,
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
