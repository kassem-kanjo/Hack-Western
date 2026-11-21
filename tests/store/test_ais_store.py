"""SQLite behaviour of the #72 AIS tables: round trips, upserts and range reads.

The Baltic detectors replay a recording through these methods, so the tests pin the two
things that would silently corrupt results: timestamps surviving as aware UTC values, and
re-ingesting the same batch updating rows instead of duplicating them. Coverage zero-filling
is a property of ``ingest.ais_tracks.coverage`` and is exercised there.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from darkfleet.core.tracks import CoverageMinute, Ping, VesselInfo
from darkfleet.store.sqlite_repo import SqliteRepo

T0 = datetime(2026, 11, 21, 5, 0, tzinfo=UTC)


def ping(
    mmsi: int = 276462000,
    *,
    t: datetime | None = None,
    sog: float | None = 7.7,
    cog: float | None = 352.0,
    heading: int | None = 358,
    nav_status: int | None = 0,
) -> Ping:
    return Ping(
        mmsi=mmsi,
        t=t or T0,
        lat=55.53446666666667,
        lon=12.717166666666666,
        sog=sog,
        cog=cog,
        heading=heading,
        nav_status=nav_status,
    )


def vessel(
    mmsi: int = 266128000,
    *,
    name: str | None = "VALKYRIA",
    last_seen: datetime | None = None,
) -> VesselInfo:
    return VesselInfo(
        mmsi=mmsi,
        imo=9096806,
        name=name,
        callsign="SMPK",
        ship_type=30,
        length_m=18.0,
        destination="-",
        last_seen=last_seen or T0,
    )


@pytest.fixture
def repo() -> Iterator[SqliteRepo]:
    instance = SqliteRepo(":memory:")
    try:
        yield instance
    finally:
        instance.close()


def test_ping_round_trip(repo: SqliteRepo) -> None:
    first = ping()
    second = ping(
        219005313,
        t=T0 + timedelta(seconds=30),
        sog=None,
        cog=None,
        heading=None,
        nav_status=None,
    )

    assert repo.add_pings([first, second]) == 2

    assert repo.pings_between(T0, T0 + timedelta(minutes=1)) == [first, second]


def test_ping_upsert_is_idempotent_and_updates_fields(repo: SqliteRepo) -> None:
    original = ping(sog=7.7)
    assert repo.add_pings([original]) == 1
    assert repo.add_pings([ping(sog=8.4, heading=None)]) == 1

    rows = repo.pings_between(T0, T0)
    assert len(rows) == 1
    assert rows[0].sog == pytest.approx(8.4)
    assert rows[0].heading is None


def test_pings_between_is_inclusive_and_ordered(repo: SqliteRepo) -> None:
    later = ping(t=T0 + timedelta(minutes=5))
    earliest = ping(t=T0)
    between = ping(t=T0 + timedelta(minutes=2, seconds=30))
    repo.add_pings([later, earliest, between])

    assert repo.pings_between(T0, T0 + timedelta(minutes=5)) == [earliest, between, later]
    assert repo.pings_between(T0, T0 + timedelta(minutes=2, seconds=30)) == [earliest, between]
    assert repo.pings_between(T0 + timedelta(minutes=1), T0 + timedelta(minutes=5)) == [
        between,
        later,
    ]


def test_vessel_round_trip_and_update(repo: SqliteRepo) -> None:
    original = vessel()
    assert repo.upsert_vessels([original]) == 1
    assert repo.vessels() == [original]

    newer = vessel(name="VALKYRIA II", last_seen=T0 + timedelta(hours=1))
    assert repo.upsert_vessels([newer]) == 1

    assert repo.vessels() == [newer]


def test_vessels_are_ordered_by_mmsi(repo: SqliteRepo) -> None:
    two = vessel(305773000)
    one = vessel(211459920, name="BUNTHAUS")
    repo.upsert_vessels([two, one])

    assert [row.mmsi for row in repo.vessels()] == [211459920, 305773000]


def test_vessel_with_unknown_fields_round_trips(repo: SqliteRepo) -> None:
    sparse = VesselInfo(mmsi=265500000)
    repo.upsert_vessels([sparse])
    assert repo.vessels() == [sparse]


def test_coverage_round_trip_and_upsert(repo: SqliteRepo) -> None:
    minutes = [
        CoverageMinute(minute=T0, n_msgs=180),
        CoverageMinute(minute=T0 + timedelta(minutes=1), n_msgs=0),
    ]
    assert repo.set_coverage(minutes) == 2
    assert repo.coverage_between(T0, T0 + timedelta(minutes=1)) == minutes

    corrected = [CoverageMinute(minute=T0, n_msgs=175)]
    assert repo.set_coverage(corrected) == 1
    assert repo.coverage_between(T0, T0) == corrected


def test_coverage_between_is_inclusive(repo: SqliteRepo) -> None:
    repo.set_coverage(
        [
            CoverageMinute(minute=T0, n_msgs=1),
            CoverageMinute(minute=T0 + timedelta(minutes=5), n_msgs=2),
        ]
    )
    assert repo.coverage_between(T0, T0 + timedelta(minutes=5)) == [
        CoverageMinute(minute=T0, n_msgs=1),
        CoverageMinute(minute=T0 + timedelta(minutes=5), n_msgs=2),
    ]
    assert repo.coverage_between(T0 + timedelta(minutes=1), T0 + timedelta(minutes=4)) == []


def test_positions_table_from_before_72_is_widened_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE positions (mmsi TEXT NOT NULL, ts TEXT NOT NULL, lat REAL NOT NULL, "
            "lon REAL NOT NULL, sog REAL, PRIMARY KEY (mmsi, ts))"
        )
        connection.commit()
    finally:
        connection.close()

    repo = SqliteRepo(path)
    try:
        original = ping()
        assert repo.add_pings([original]) == 1
        assert repo.pings_between(T0, T0) == [original]
    finally:
        repo.close()
