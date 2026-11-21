"""Offline tests for scripts/build_unseen_fleet.py using fixture SAR rows."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from darkfleet.ingest.gfw import SarCellRow, parse_sar_report
from darkfleet.store.sqlite_repo import SqliteRepo

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_unseen_fleet.py"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gfw" / "sar_4wings_report_unmatched.json"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_unseen_fleet", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_unseen_fleet = _load_script()


def _fixture_rows() -> list[SarCellRow]:
    return parse_sar_report(json.loads(FIXTURE.read_text(encoding="utf-8")))


# Real response rows, split arbitrarily into the two request classes; the fake client
# filters by the requested window just like the real endpoint does.
ALL_ROWS = _fixture_rows()
MATCHED_ROWS = ALL_ROWS[:5]
UNMATCHED_ROWS = ALL_ROWS[5:]
START = date(2026, 8, 15)
END = date(2026, 11, 15)


class FakeClient:
    """Returns fixture rows and records every chunk it is asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[date, date, bool | None]] = []

    def sar_cells(
        self,
        region,
        start: date,
        end: date,
        unmatched_only: bool | None = None,
        *,
        matched: bool | None = None,
    ) -> list[SarCellRow]:
        self.calls.append((start, end, matched))
        rows = MATCHED_ROWS if matched is True else UNMATCHED_ROWS if matched is False else ALL_ROWS
        return [row for row in rows if start <= row.day <= end]


def test_build_snapshot_shape_and_arithmetic(tmp_path: Path) -> None:
    payload = build_unseen_fleet.build_snapshot(FakeClient(), "HORMUZ", START, END)

    assert payload["region"] == "HORMUZ"
    assert payload["period"] == {"start": "2026-08-15", "end": "2026-11-15"}
    assert payload["source"] == "Global Fishing Watch SAR vessel detections (Sentinel-1)"
    assert payload["caveats"][:3] == [
        "Radar misses vessels under ~25 m",
        "'Dark' means no AIS match, not wrongdoing",
        "Daily resolution",
    ]
    if payload["summary"]["n_days_excluded"]:
        assert "excluded" in payload["caveats"][3]
    else:
        assert payload["caveats"] == [
            "Radar misses vessels under ~25 m",
            "'Dark' means no AIS match, not wrongdoing",
            "Daily resolution",
        ]

    daily = payload["daily"]
    assert daily
    assert [item["day"] for item in daily] == sorted(item["day"] for item in daily)
    for item in daily:
        assert item["seen"] == item["broadcasting"] + item["dark"]

    # Every fixture row is inside the window, so no detection may be lost or doubled.
    seen = sum(row.detections for row in ALL_ROWS)
    dark = sum(row.detections for row in UNMATCHED_ROWS)
    assert payload["summary"]["seen_total"] == seen
    assert payload["summary"]["dark_total"] == dark
    assert payload["summary"]["days"] == len(daily)
    assert set(payload["summary"]) == {
        "days",
        "seen_total",
        "dark_total",
        "dark_share_mean",
        "dark_share_ci",
        "median_dark_share",
        "iqr",
        "headline_share",
        "n_days_used",
        "n_days_excluded",
        "excluded_days",
        "busiest_day",
    }
    assert payload["summary"]["n_days_used"] + payload["summary"]["n_days_excluded"] == len(daily)

    for cell in payload["grid"]:
        assert set(cell) == {"lat", "lon", "n"}
        assert cell["n"] > 0


class _RowsClient:
    """Return a fixed matched/unmatched series, filtered by the requested window."""

    def __init__(self, matched: list[SarCellRow], unmatched: list[SarCellRow]) -> None:
        self.matched = matched
        self.unmatched = unmatched

    def sar_cells(
        self,
        region,
        start: date,
        end: date,
        unmatched_only: bool | None = None,
        *,
        matched: bool | None = None,
    ) -> list[SarCellRow]:
        rows = self.matched if matched is True else self.unmatched
        return [row for row in rows if start <= row.day <= end]


def test_low_coverage_days_are_excluded_and_named_in_the_caveats() -> None:
    days = [date(2026, 10, offset) for offset in range(1, 6)]
    seen = [100, 100, 100, 100, 2]  # the last day is a partial satellite pass
    dark = [5, 10, 20, 40, 2]
    matched = [
        SarCellRow(day=day, lat=25.0, lon=56.0, detections=total - dark_n, vessel_ids=1)
        for day, total, dark_n in zip(days, seen, dark, strict=True)
    ]
    unmatched = [
        SarCellRow(day=day, lat=25.0, lon=56.0, detections=dark_n, vessel_ids=1)
        for day, dark_n in zip(days, dark, strict=True)
    ]
    start, end = days[0], days[-1]

    payload = build_unseen_fleet.build_snapshot(_RowsClient(matched, unmatched), "HORMUZ", start, end)

    headline = payload["summary"]
    assert headline["days"] == 5
    assert headline["n_days_used"] == 4
    assert headline["n_days_excluded"] == 1
    assert headline["excluded_days"] == ["2026-10-05"]
    assert headline["headline_share"] == headline["median_dark_share"]
    assert any("excluded" in caveat and "2026-10-05" in caveat for caveat in payload["caveats"])


def test_chunks_cover_the_window_in_at_most_31_days(tmp_path: Path) -> None:
    out = tmp_path / "unseen.json"
    client = FakeClient()

    code = build_unseen_fleet.main(
        ["--start", START.isoformat(), "--end", END.isoformat(), "--out", str(out)],
        client=client,
    )

    assert code == 0
    assert out.is_file()
    matched_calls = [call for call in client.calls if call[2] is True]
    assert len(matched_calls) == 3
    assert len(client.calls) == 6
    for start, end, _ in client.calls:
        assert start <= end
        assert (end - start).days + 1 <= 31
    assert matched_calls[0] == (START, date(2026, 9, 14), True)
    assert matched_calls[1] == (date(2026, 9, 15), date(2026, 10, 15), True)
    assert matched_calls[2] == (date(2026, 10, 16), END, True)
    # Chunks are contiguous: each one starts the day after the previous one ends.
    for previous, current in zip(matched_calls, matched_calls[1:], strict=False):
        assert current[0] == previous[1] + timedelta(days=1)


def test_to_db_writes_the_daily_series_idempotently(tmp_path: Path) -> None:
    out = tmp_path / "unseen.json"
    repo = SqliteRepo(tmp_path / "fleet.sqlite")
    args = [
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--out",
        str(out),
        "--to-db",
    ]

    assert build_unseen_fleet.main(args, client=FakeClient(), repo=repo) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    first_count = len(payload["daily"])
    repo.close()

    # The second run must upsert, not duplicate.
    repo = SqliteRepo(tmp_path / "fleet.sqlite")
    assert build_unseen_fleet.main(args, client=FakeClient(), repo=repo) == 0
    repo.close()

    with sqlite3.connect(tmp_path / "fleet.sqlite") as conn:
        rows = conn.execute(
            "SELECT day, region, seen, broadcasting, dark FROM sar_daily ORDER BY day"
        ).fetchall()

    assert len(rows) == first_count
    assert [row[0] for row in rows] == [item["day"] for item in payload["daily"]]
    for _, region, seen, broadcasting, dark in rows:
        assert region == "HORMUZ"
        assert seen == broadcasting + dark


def test_end_before_start_is_rejected(tmp_path: Path) -> None:
    out = tmp_path / "unseen.json"

    code = build_unseen_fleet.main(
        ["--start", "2026-11-15", "--end", "2026-08-15", "--out", str(out)],
        client=FakeClient(),
    )

    assert code == 2
    assert not out.exists()
