"""Daily dark-hours buckets: three synthetic gaps spanning two UTC days."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest

from darkfleet.ingest.gfw import GapEvent
from darkfleet.store.sqlite_repo import SqliteRepo

D1 = date(2026, 3, 1)
D2 = date(2026, 3, 2)


def gap(gap_id: str, *, start: datetime, duration_hours: float) -> GapEvent:
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
def repo() -> Iterator[SqliteRepo]:
    instance = SqliteRepo()
    try:
        yield instance
    finally:
        instance.close()


def three_gaps(repo: SqliteRepo) -> None:
    repo.upsert_gaps(
        "HORMUZ",
        [
            gap("gap-early", start=datetime(2026, 3, 1, 1, 0, tzinfo=UTC), duration_hours=2.0),
            gap("gap-late", start=datetime(2026, 3, 1, 22, 0, tzinfo=UTC), duration_hours=3.5),
            gap("gap-next", start=datetime(2026, 3, 2, 9, 0, tzinfo=UTC), duration_hours=1.0),
        ],
    )


def test_three_gaps_bucket_into_two_days(repo: SqliteRepo) -> None:
    three_gaps(repo)

    assert repo.dark_hours_daily(D1, D2) == [
        {"day": D1, "hours": 5.5, "gaps": 2},
        {"day": D2, "hours": 1.0, "gaps": 1},
    ]


def test_range_is_inclusive_and_refresh_flag_is_accepted(repo: SqliteRepo) -> None:
    three_gaps(repo)

    assert repo.dark_hours_daily(D2, D2, refresh=True) == [{"day": D2, "hours": 1.0, "gaps": 1}]
    assert repo.dark_hours_daily(date(2026, 3, 3), date(2026, 3, 4)) == []


def test_gap_is_attributed_to_its_start_day(repo: SqliteRepo) -> None:
    repo.upsert_gaps(
        "HORMUZ",
        [gap("gap-crossing", start=datetime(2026, 3, 1, 23, 0, tzinfo=UTC), duration_hours=4.0)],
    )

    assert repo.dark_hours_daily(D1, D2) == [{"day": D1, "hours": 4.0, "gaps": 1}]
