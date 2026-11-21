"""Offline test of scripts/ingest_region.py: a fake GFW client feeds a SQLite repo."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow
from darkfleet.store.sqlite_repo import SqliteRepo

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_region.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("ingest_region", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gap(i: int) -> GapEvent:
    t0 = datetime(2026, 10, 3, 6, tzinfo=UTC)
    return GapEvent(
        id=f"gap-{i}",
        vessel_id=f"v-{i}",
        mmsi=str(400000000 + i),
        vessel_name=None,
        flag="PAN",
        vessel_type="tanker",
        start=t0,
        end=datetime(2026, 10, 3, 18, tzinfo=UTC),
        off_lat=25.0,
        off_lon=56.0,
        on_lat=25.5,
        on_lon=56.5,
        duration_hours=12.0,
        implied_speed_knots=3.0,
        distance_km=20.0,
        intentional_disabling=True,
    )


def _encounter(i: int) -> EncounterEvent:
    t0 = datetime(2026, 10, 4, 6, tzinfo=UTC)
    return EncounterEvent(
        id=f"enc-{i}",
        start=t0,
        end=datetime(2026, 10, 4, 8, tzinfo=UTC),
        vessel_id="a",
        mmsi="1",
        vessel_name=None,
        flag=None,
        vessel_type=None,
        encountered_vessel_id="b",
        encountered_mmsi="2",
        encountered_name=None,
        encountered_flag=None,
        lat=25.0,
        lon=56.0,
        median_distance_km=0.2,
        median_speed_knots=1.0,
        encounter_type="carrier-tanker",
        potential_risk=False,
    )


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def gaps(self, region, start, end):
        self.calls.append(("gaps", start, end))
        return [_gap(1), _gap(2)]

    def encounters(self, region, start, end):
        self.calls.append(("encounters", start, end))
        return [_encounter(1)]

    def sar_cells(self, region, start, end, unmatched_only=True):
        assert unmatched_only is True
        self.calls.append(("sar", start, end))
        return [
            SarCellRow(day=date(2026, 10, 7), lat=25.16, lon=56.58, detections=1, vessel_ids=1),
            SarCellRow(day=date(2026, 10, 7), lat=25.17, lon=56.58, detections=1, vessel_ids=1),
        ]


def test_ingest_region_writes_all_three_feeds(tmp_path, capsys) -> None:
    module = _load_script()
    client = FakeClient()
    repo = SqliteRepo(tmp_path / "store.sqlite")

    code = module.main(
        ["--region", "HORMUZ", "--start", "2026-10-01", "--end", "2026-10-31"],
        client=client,
        repo=repo,
    )

    assert code == 0
    assert [c[0] for c in client.calls] == ["gaps", "encounters", "sar"]
    assert len(repo.gaps_in("HORMUZ", date(2026, 10, 1), date(2026, 10, 31))) == 2
    assert len(repo.encounters_in("HORMUZ", date(2026, 10, 1), date(2026, 10, 31))) == 1
    assert len(repo.sar_cells_in("HORMUZ", date(2026, 10, 1), date(2026, 10, 31))) == 2
    out = capsys.readouterr().out
    assert "gaps fetched=2 written=2" in out
    assert "encounters fetched=1 written=1" in out
    assert "sar_cells_unmatched fetched=2 written=2" in out


def test_ingest_region_rejects_inverted_range(tmp_path) -> None:
    module = _load_script()
    code = module.main(
        ["--region", "HORMUZ", "--start", "2026-10-31", "--end", "2026-10-01"],
        client=FakeClient(),
        repo=SqliteRepo(tmp_path / "x.sqlite"),
    )
    assert code == 2


def test_ingest_region_rejects_unknown_region() -> None:
    module = _load_script()
    try:
        module.main(
            ["--region", "NOWHERE", "--start", "2026-10-01", "--end", "2026-10-02"], client=FakeClient()
        )
    except SystemExit as exc:
        assert exc.code != 0
    else:  # pragma: no cover - argparse must reject the name
        raise AssertionError("unknown region was accepted")
