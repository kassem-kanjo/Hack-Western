from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow
from darkfleet.store.sqlite_repo import SqliteRepo

D1 = date(2026, 3, 1)
D2 = date(2026, 3, 2)
D3 = date(2026, 3, 3)


def gap(
    gap_id: str = "gap-1",
    *,
    start: datetime | None = None,
    duration_hours: float = 2.0,
    vessel_name: str = "MV Test",
) -> GapEvent:
    begin = start or datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    return GapEvent(
        id=gap_id,
        vessel_id="vessel-1",
        mmsi="123456789",
        vessel_name=vessel_name,
        flag="PA",
        vessel_type="cargo",
        start=begin,
        end=begin + timedelta(hours=duration_hours),
        off_lat=25.0,
        off_lon=56.0,
        on_lat=25.5,
        on_lon=56.5,
        duration_hours=duration_hours,
        implied_speed_knots=12.5,
        distance_km=80.0,
        intentional_disabling=False,
    )


def encounter(enc_id: str = "enc-1", *, start: datetime | None = None) -> EncounterEvent:
    begin = start or datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    return EncounterEvent(
        id=enc_id,
        start=begin,
        end=begin + timedelta(hours=1),
        vessel_id="vessel-1",
        mmsi="123456789",
        vessel_name="MV Test",
        flag="PA",
        vessel_type="cargo",
        encountered_vessel_id="vessel-2",
        encountered_mmsi="987654321",
        encountered_name="MV Other",
        encountered_flag="LR",
        lat=25.2,
        lon=56.2,
        median_distance_km=0.4,
        median_speed_knots=3.1,
        encounter_type="partial",
        potential_risk=True,
    )


def sar_cell(day: date, lat: float, lon: float, detections: int = 3) -> SarCellRow:
    return SarCellRow(day=day, lat=lat, lon=lon, detections=detections, vessel_ids=1)


@pytest.fixture
def repo() -> Iterator[SqliteRepo]:
    instance = SqliteRepo(":memory:")
    try:
        yield instance
    finally:
        instance.close()


def test_gap_round_trip(repo: SqliteRepo) -> None:
    original = gap()
    assert repo.upsert_gaps("HORMUZ", [original]) == 1

    assert repo.gaps_in("HORMUZ", D1, D1) == [original]


def test_encounter_round_trip(repo: SqliteRepo) -> None:
    original = encounter()
    assert repo.upsert_encounters("HORMUZ", [original]) == 1

    assert repo.encounters_in("HORMUZ", D1, D1) == [original]


def test_gap_upsert_twice_is_idempotent(repo: SqliteRepo) -> None:
    original = gap()
    assert repo.upsert_gaps("HORMUZ", [original]) == 1
    assert repo.upsert_gaps("HORMUZ", [original]) == 1

    assert repo.gaps_in("HORMUZ", D1, D1) == [original]


def test_gap_upsert_keeps_rowid_stable(repo: SqliteRepo) -> None:
    original = gap()
    repo.upsert_gaps("HORMUZ", [original])
    rowid_before = repo._conn.execute("SELECT rowid FROM gap_events").fetchone()[0]

    repo.upsert_gaps("HORMUZ", [original])

    assert repo._conn.execute("SELECT rowid FROM gap_events").fetchone()[0] == rowid_before


def test_gap_upsert_updates_changed_fields(repo: SqliteRepo) -> None:
    repo.upsert_gaps("HORMUZ", [gap(duration_hours=2.0, vessel_name="Old Name")])
    changed = gap(duration_hours=4.5, vessel_name="New Name")
    assert repo.upsert_gaps("HORMUZ", [changed]) == 1

    rows = repo.gaps_in("HORMUZ", D1, D1)
    assert len(rows) == 1
    assert rows[0].vessel_name == "New Name"
    assert rows[0].duration_hours == 4.5


def test_region_filter_isolates_regions(repo: SqliteRepo) -> None:
    repo.upsert_gaps("HORMUZ", [gap("gap-hormuz")])
    repo.upsert_encounters("HORMUZ", [encounter("enc-hormuz")])
    repo.upsert_sar_cells("HORMUZ", [sar_cell(D1, 25.0, 56.0)])
    repo.upsert_gaps("MALACCA", [gap("gap-malacca")])
    repo.upsert_encounters("MALACCA", [encounter("enc-malacca")])
    repo.upsert_sar_cells("MALACCA", [sar_cell(D1, 25.0, 56.0)])

    assert [row.id for row in repo.gaps_in("HORMUZ", D1, D3)] == ["gap-hormuz"]
    assert [row.id for row in repo.encounters_in("HORMUZ", D1, D3)] == ["enc-hormuz"]
    assert [row.id for row in repo.gaps_in("MALACCA", D1, D3)] == ["gap-malacca"]
    assert [row.id for row in repo.encounters_in("MALACCA", D1, D3)] == ["enc-malacca"]
    assert len(repo.sar_cells_in("HORMUZ", D1, D3)) == 1
    assert len(repo.sar_cells_in("MALACCA", D1, D3)) == 1


def test_date_range_is_inclusive_on_both_edges(repo: SqliteRepo) -> None:
    on_start = gap("gap-start", start=datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
    on_end = gap("gap-end", start=datetime(2026, 3, 2, 23, 59, tzinfo=UTC))
    after = gap("gap-after", start=datetime(2026, 3, 3, 0, 0, tzinfo=UTC))
    repo.upsert_gaps("HORMUZ", [on_start, on_end, after])

    assert [row.id for row in repo.gaps_in("HORMUZ", D1, D2)] == ["gap-start", "gap-end"]


def test_encounter_date_range_is_inclusive(repo: SqliteRepo) -> None:
    on_start = encounter("enc-start", start=datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
    on_end = encounter("enc-end", start=datetime(2026, 3, 2, 23, 59, tzinfo=UTC))
    after = encounter("enc-after", start=datetime(2026, 3, 3, 0, 0, tzinfo=UTC))
    repo.upsert_encounters("HORMUZ", [on_start, on_end, after])

    assert [row.id for row in repo.encounters_in("HORMUZ", D1, D2)] == ["enc-start", "enc-end"]


def test_gaps_are_ordered_by_start(repo: SqliteRepo) -> None:
    late = gap("gap-late", start=datetime(2026, 3, 1, 18, 0, tzinfo=UTC))
    early = gap("gap-early", start=datetime(2026, 3, 1, 6, 0, tzinfo=UTC))
    repo.upsert_gaps("HORMUZ", [late, early])

    assert [row.id for row in repo.gaps_in("HORMUZ", D1, D1)] == ["gap-early", "gap-late"]


def test_sar_cells_ordered_by_day_lat_lon(repo: SqliteRepo) -> None:
    cells = [
        sar_cell(D2, 25.0, 56.0),
        sar_cell(D1, 26.0, 56.0),
        sar_cell(D1, 25.5, 56.0),
    ]
    assert repo.upsert_sar_cells("HORMUZ", cells) == 3

    assert [(c.day, c.lat) for c in repo.sar_cells_in("HORMUZ", D1, D2)] == [
        (D1, 25.5),
        (D1, 26.0),
        (D2, 25.0),
    ]


def test_sar_cell_upsert_overwrites_detections(repo: SqliteRepo) -> None:
    repo.upsert_sar_cells("HORMUZ", [sar_cell(D1, 25.0, 56.0, detections=3)])
    repo.upsert_sar_cells(
        "HORMUZ",
        [SarCellRow(day=D1, lat=25.0, lon=56.0, detections=9, vessel_ids=4)],
    )

    rows = repo.sar_cells_in("HORMUZ", D1, D1)
    assert rows == [SarCellRow(day=D1, lat=25.0, lon=56.0, detections=9, vessel_ids=4)]


def test_sar_date_range_is_inclusive(repo: SqliteRepo) -> None:
    repo.upsert_sar_cells("HORMUZ", [sar_cell(D1, 25.0, 56.0), sar_cell(D3, 25.0, 56.0)])

    assert [c.day for c in repo.sar_cells_in("HORMUZ", D1, D2)] == [D1]


def test_positions_upsert_is_idempotent(repo: SqliteRepo) -> None:
    from darkfleet.store.repository import PositionRow

    row = PositionRow(
        ts=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        mmsi="123456789",
        lat=25.0,
        lon=56.0,
        sog=12.0,
    )
    assert repo.upsert_positions([row]) == 1
    assert repo.upsert_positions([row]) == 1

    count = repo._conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert count == 1


def test_dark_hours_daily(repo: SqliteRepo) -> None:
    from darkfleet.store.repository import DarkHoursDay

    late_day_one = gap(
        "gap-late",
        start=datetime(2026, 3, 1, 22, 0, tzinfo=UTC),
        duration_hours=3.5,
    )
    day_one = gap("gap-early", start=datetime(2026, 3, 1, 1, 0, tzinfo=UTC), duration_hours=2.0)
    day_two = gap("gap-two", start=datetime(2026, 3, 2, 9, 0, tzinfo=UTC), duration_hours=1.0)
    repo.upsert_gaps("HORMUZ", [day_one, late_day_one, day_two])

    assert repo.dark_hours_daily("HORMUZ") == [
        DarkHoursDay(day=D1, dark_hours=5.5, gap_count=2),
        DarkHoursDay(day=D2, dark_hours=1.0, gap_count=1),
    ]


def test_dark_hours_daily_scoped_to_region(repo: SqliteRepo) -> None:
    from darkfleet.store.repository import DarkHoursDay

    repo.upsert_gaps("HORMUZ", [gap("gap-h", duration_hours=2.0)])
    repo.upsert_gaps("MALACCA", [gap("gap-m", duration_hours=9.0)])

    assert repo.dark_hours_daily("HORMUZ") == [DarkHoursDay(day=D1, dark_hours=2.0, gap_count=1)]


def test_file_backed_repo_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "cache" / "darkfleet.sqlite"
    repo = SqliteRepo(path)
    try:
        repo.upsert_gaps("HORMUZ", [gap()])
    finally:
        repo.close()

    assert path.exists()
    reopened = SqliteRepo(path)
    try:
        assert [row.id for row in reopened.gaps_in("HORMUZ", D1, D1)] == ["gap-1"]
    finally:
        reopened.close()
