"""Issue #208: the live AIS sink resumes from offsets and only sends new pings.

Every test uses a fake repository, so no database is needed (and none is touched).
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from darkfleet.core.tracks import Ping

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tiger_sink.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("tiger_sink", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tiger_sink = _load_script()

T0 = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


class FakeRepo:
    """Collects the batches the sink would have written."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Ping]]] = []

    def add_region_pings(self, region: str, pings: Any) -> int:
        self.calls.append((region, list(pings)))
        return len(pings)

    @property
    def ping_count(self) -> int:
        return sum(len(pings) for _, pings in self.calls)


def _stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") + f".{t.microsecond:06d}000 +0000 UTC"


def _position(mmsi: int, t: datetime) -> dict[str, Any]:
    return {
        "MetaData": {"MMSI": mmsi, "time_utc": _stamp(t)},
        "MessageType": "PositionReport",
        "Message": {
            "PositionReport": {
                "Latitude": 55.0,
                "Longitude": 12.0,
                "Sog": 8.0,
                "Cog": 90.0,
                "TrueHeading": 90,
            }
        },
    }


def _write_hour(root: Path, region: str, hour: str, pings: list[dict[str, Any]]) -> Path:
    directory = root / region
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hour}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for ping in pings:
            handle.write(json.dumps(ping) + "\n")
    return path


def _read_offsets(path: Path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_offsets_resume_across_a_restart(tmp_path: Path) -> None:
    root = tmp_path / "ais"
    offsets_path = tmp_path / "offsets.json"
    _write_hour(
        root,
        "BALTIC_WATCH",
        "20261121T09",
        [_position(111111111, T0 + timedelta(minutes=i)) for i in range(3)],
    )

    first = FakeRepo()
    assert tiger_sink.run_once(first, root, offsets_path) == (3, 1)
    assert first.ping_count == 3
    assert len(_read_offsets(offsets_path)) == 1

    # A restart has no in-memory mtimes, so it re-reads the file, sees three pings already
    # sent and sends nothing.
    second = FakeRepo()
    assert tiger_sink.run_once(second, root, offsets_path) == (0, 0)
    assert second.calls == []


def test_only_pings_after_the_offset_are_sent_when_a_file_grows(tmp_path: Path) -> None:
    root = tmp_path / "ais"
    offsets_path = tmp_path / "offsets.json"
    first_two = [_position(111111111, T0), _position(222222222, T0 + timedelta(minutes=1))]
    path = _write_hour(root, "BALTIC_WATCH", "20261121T09", first_two)

    repo = FakeRepo()
    mtimes: dict[str, int] = {}
    assert tiger_sink.run_once(repo, root, offsets_path, mtimes=mtimes) == (2, 1)

    # The recorder appends while we sleep; the rewritten file's mtime changes.
    third = _position(333333333, T0 + timedelta(minutes=2))
    _write_hour(root, "BALTIC_WATCH", "20261121T09", [*first_two, third])
    assert path.stat().st_mtime_ns != 0

    sent, files = tiger_sink.run_once(repo, root, offsets_path, mtimes=mtimes)
    assert (sent, files) == (1, 1)
    assert repo.ping_count == 3
    assert [ping.mmsi for ping in repo.calls[-1][1]] == [333333333]


def test_unchanged_files_are_not_re_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ais"
    offsets_path = tmp_path / "offsets.json"
    _write_hour(root, "BALTIC_WATCH", "20261121T09", [_position(111111111, T0)])

    repo = FakeRepo()
    mtimes: dict[str, int] = {}
    assert tiger_sink.run_once(repo, root, offsets_path, mtimes=mtimes) == (1, 1)

    reads: list[Path] = []

    def fail_if_read(path: Path) -> list[Ping]:
        reads.append(path)
        return []

    monkeypatch.setattr(tiger_sink, "_pings", fail_if_read)
    assert tiger_sink.run_once(repo, root, offsets_path, mtimes=mtimes) == (0, 0)
    assert reads == []


def test_pings_are_batched_per_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tiger_sink, "BATCH_SIZE", 2)
    root = tmp_path / "ais"
    offsets_path = tmp_path / "offsets.json"
    for region in ("BALTIC_WATCH", "HORMUZ"):
        _write_hour(
            root,
            region,
            "20261121T09",
            [_position(111111111 + i, T0 + timedelta(minutes=i)) for i in range(5)],
        )

    repo = FakeRepo()
    sent, files = tiger_sink.run_once(repo, root, offsets_path)

    assert (sent, files) == (10, 2)
    assert sorted(region for region, _ in repo.calls) == ["BALTIC_WATCH"] * 3 + ["HORMUZ"] * 3
    assert [len(pings) for _, pings in repo.calls] == [2, 2, 1, 2, 2, 1]


def test_missing_database_url_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = tmp_path / "ais"
    root.mkdir()

    result = tiger_sink.main(["--ais", str(root), "--offsets", str(tmp_path / "offsets.json")])

    assert result != 0


def test_main_runs_one_pass_with_an_injected_repo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ais"
    offsets_path = tmp_path / "offsets.json"
    _write_hour(
        root,
        "BALTIC_WATCH",
        "20261121T09",
        [_position(111111111, T0), _position(222222222, T0 + timedelta(minutes=1))],
    )

    repo = FakeRepo()
    result = tiger_sink.main(
        ["--ais", str(root), "--offsets", str(offsets_path), "--loop", "0"],
        repo=repo,
    )

    assert result == 0
    assert repo.ping_count == 2
    assert "sent 2 pings, 1 files" in capsys.readouterr().out


def test_corrupt_offsets_resume_from_zero(tmp_path: Path) -> None:
    path = tmp_path / "offsets.json"
    path.write_text("not json", encoding="utf-8")

    assert tiger_sink.load_offsets(path) == {}
