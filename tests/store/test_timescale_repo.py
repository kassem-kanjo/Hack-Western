from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from darkfleet.ingest.gfw import GapEvent
from darkfleet.store.repository import DarkHoursDay
from darkfleet.store.timescale_repo import TimescaleRepo

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL is not set")


def gap(
    gap_id: str,
    *,
    start: datetime,
    duration_hours: float,
) -> GapEvent:
    return GapEvent(
        id=gap_id,
        vessel_id="vessel-1",
        mmsi="123456789",
        vessel_name="MV Test",
        flag="PA",
        vessel_type="cargo",
        start=start,
        end=start + timedelta(hours=duration_hours),
        off_lat=25.0,
        off_lon=56.0,
        on_lat=25.5,
        on_lon=56.5,
        duration_hours=duration_hours,
        implied_speed_knots=12.5,
        distance_km=80.0,
        intentional_disabling=False,
    )


@pytest.fixture
def repo() -> Iterator[TimescaleRepo]:
    assert TEST_URL is not None
    schema_name = f"df_test_{uuid.uuid4().hex}"
    admin = psycopg.connect(TEST_URL, autocommit=True)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        instance = TimescaleRepo(TEST_URL, schema=schema_name)
        try:
            yield instance
        finally:
            instance.close()
    finally:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name)))
        admin.close()


def test_gap_round_trip_and_idempotency(repo: TimescaleRepo) -> None:
    start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    original = gap("gap-1", start=start, duration_hours=2.0)

    assert repo.upsert_gaps("HORMUZ", [original]) == 1
    assert repo.upsert_gaps("HORMUZ", [original]) == 1

    assert repo.gaps_in("HORMUZ", date(2026, 3, 1), date(2026, 3, 1)) == [original]


def test_dark_hours_daily_uses_continuous_aggregate(repo: TimescaleRepo) -> None:
    day_one = date(2026, 3, 1)
    day_two = date(2026, 3, 2)
    repo.upsert_gaps(
        "HORMUZ",
        [
            gap("gap-early", start=datetime(2026, 3, 1, 1, 0, tzinfo=UTC), duration_hours=2.0),
            gap("gap-late", start=datetime(2026, 3, 1, 22, 0, tzinfo=UTC), duration_hours=3.5),
            gap("gap-two", start=datetime(2026, 3, 2, 9, 0, tzinfo=UTC), duration_hours=1.0),
        ],
    )

    assert repo.dark_hours_daily("HORMUZ") == [
        DarkHoursDay(day=day_one, dark_hours=5.5, gap_count=2),
        DarkHoursDay(day=day_two, dark_hours=1.0, gap_count=1),
    ]
