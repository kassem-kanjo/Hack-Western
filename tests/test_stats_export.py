"""Issue #31: the export carries the Tiger continuous aggregates, not just dark hours.

Both tests use the SQLite backend on a temp file; no real database is ever touched.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from darkfleet.core.tracks import Ping
from darkfleet.ingest.gfw import GapEvent
from darkfleet.store.repository import SarDailyRow
from darkfleet.store.sqlite_repo import SqliteRepo

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_stats.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("export_stats", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_stats = _load_script()

T0 = datetime(2026, 11, 20, 22, 0, tzinfo=UTC)


def ping(mmsi: int, *, t: datetime) -> Ping:
    return Ping(mmsi=mmsi, t=t, lat=55.5, lon=12.7, sog=8.0, cog=90.0, heading=90, nav_status=0)


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
def repo(tmp_path: Path) -> Iterator[SqliteRepo]:
    instance = SqliteRepo(tmp_path / "darkfleet.sqlite")
    try:
        yield instance
    finally:
        instance.close()


def seed_days(repo: SqliteRepo) -> None:
    """Two radar regions in the week of 16 Nov, one row in the next week."""
    repo.upsert_sar_daily(
        [
            SarDailyRow(day=date(2026, 11, 16), region="HORMUZ", seen=10, broadcasting=6, dark=4),
            SarDailyRow(day=date(2026, 11, 17), region="BALTIC_WATCH", seen=5, broadcasting=2, dark=3),
            SarDailyRow(day=date(2026, 11, 23), region="HORMUZ", seen=2, broadcasting=1, dark=1),
        ]
    )


def test_ais_hourly_groups_positions_into_utc_hours(repo: SqliteRepo) -> None:
    repo.add_pings(
        [
            ping(111111111, t=T0),
            ping(111111111, t=T0 + timedelta(minutes=30)),
            ping(222222222, t=T0 + timedelta(hours=1, minutes=15)),
        ]
    )

    assert repo.ais_hourly(T0, T0 + timedelta(hours=2)) == [
        {"hour": T0, "msgs": 2},
        {"hour": T0 + timedelta(hours=1), "msgs": 1},
    ]
    # The range is half-open, so an hour starting at the end is not included.
    assert repo.ais_hourly(T0, T0 + timedelta(hours=1)) == [{"hour": T0, "msgs": 2}]


def test_sar_weekly_matches_the_timescale_buckets(repo: SqliteRepo) -> None:
    seed_days(repo)

    assert repo.sar_weekly(date(2026, 11, 16), date(2026, 11, 29)) == [
        {"week": date(2026, 11, 16), "region": "BALTIC_WATCH", "seen": 5, "broadcasting": 2, "dark": 3},
        {"week": date(2026, 11, 16), "region": "HORMUZ", "seen": 10, "broadcasting": 6, "dark": 4},
        {"week": date(2026, 11, 23), "region": "HORMUZ", "seen": 2, "broadcasting": 1, "dark": 1},
    ]


def test_build_snapshot_keeps_old_keys_and_adds_the_new_ones(repo: SqliteRepo) -> None:
    repo.upsert_gaps(
        "HORMUZ",
        [
            gap("gap-early", start=datetime(2026, 11, 16, 1, 0, tzinfo=UTC), duration_hours=2.0),
            gap("gap-late", start=datetime(2026, 11, 17, 22, 0, tzinfo=UTC), duration_hours=1.5),
        ],
    )
    seed_days(repo)
    # 60 hourly buckets: the export keeps only the last 48, ending at the latest hour.
    hours = [T0 + timedelta(hours=1) - timedelta(hours=index) for index in range(60)]
    repo.add_pings([ping(111111111, t=hour) for hour in hours])

    snapshot = export_stats.build_snapshot(repo, date(2026, 11, 1), date(2026, 11, 30))

    assert snapshot["source"] == "sqlite"
    assert snapshot["validation"] is None
    assert snapshot["dark_hours_daily"] == [
        {"day": "2026-11-16", "hours": 2.0, "gaps": 1},
        {"day": "2026-11-17", "hours": 1.5, "gaps": 1},
    ]
    assert snapshot["sar_weekly"] == [
        {
            "week": "2026-11-16",
            "region": "BALTIC_WATCH",
            "seen": 5,
            "broadcasting": 2,
            "dark": 3,
        },
        {"week": "2026-11-16", "region": "HORMUZ", "seen": 10, "broadcasting": 6, "dark": 4},
        {"week": "2026-11-23", "region": "HORMUZ", "seen": 2, "broadcasting": 1, "dark": 1},
    ]
    assert len(snapshot["ais_hourly"]) == 48
    assert snapshot["ais_hourly"][0] == {"hour": "2026-11-19T00:00:00Z", "msgs": 1}
    assert snapshot["ais_hourly"][-1] == {"hour": "2026-11-20T23:00:00Z", "msgs": 1}


def test_main_writes_every_key_to_stats_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database = tmp_path / "darkfleet.sqlite"
    seeded = SqliteRepo(database)
    try:
        seeded.upsert_gaps(
            "HORMUZ",
            [gap("gap-one", start=datetime(2026, 11, 16, 3, 0, tzinfo=UTC), duration_hours=2.0)],
        )
    finally:
        seeded.close()

    output = tmp_path / "snapshot" / "stats.json"
    exit_code = export_stats.main(
        [
            "--start",
            "2026-11-01",
            "--end",
            "2026-11-21",
            "--sqlite",
            str(database),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"dark_hours_daily", "sar_weekly", "ais_hourly", "source", "validation"}
    assert payload["source"] == "sqlite"
    assert payload["dark_hours_daily"] == [{"day": "2026-11-16", "hours": 2.0, "gaps": 1}]
    assert payload["sar_weekly"] == []
    assert payload["ais_hourly"] == []
