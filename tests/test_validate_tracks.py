"""Tests for scripts/validate_tracks.py file mode (issue #74).

In ``--jsonl-dir`` mode the coverage series must come from the files being read, so the
silence-recall test runs there just as it does against the store, and neither report may
print the caller's absolute path.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_tracks.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("validate_tracks", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can look the module up while decorating.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_tracks = _load_script()


def _position_line(t: str, *, mmsi: int = 211234567, sog: float = 8.0) -> str:
    """One aisstream class-B position report at the given ``time_utc`` stamp."""
    record = {
        "MetaData": {"MMSI": mmsi, "time_utc": t},
        "MessageType": "StandardClassBPositionReport",
        "Message": {
            "StandardClassBPositionReport": {
                "UserID": mmsi,
                "Latitude": 54.5,
                "Longitude": 10.25,
                "Sog": sog,
                "Cog": 91.0,
                "TrueHeading": 511,
            }
        },
    }
    return json.dumps(record)


def _write_jsonl_gz(path: Path, lines: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _args(jsonl_dir: Path, *, min_pings: int = 2) -> argparse.Namespace:
    return argparse.Namespace(
        jsonl_dir=str(jsonl_dir),
        seed=7,
        max_vessels=300,
        min_pings=min_pings,
        ping_gap_min=2.0,
        max_cases=250,
        decoys=20,
        max_detect_tracks=200,
        gaps_per_track=3,
        min_gap_min=60.0,
        max_fp_tracks=200,
    )


def test_jsonl_mode_builds_coverage_and_runs_the_recall_test(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(validate_tracks, "ROOT", tmp_path)
    directory = tmp_path / "data" / "cache" / "ais"
    directory.mkdir(parents=True)
    _write_jsonl_gz(
        directory / "20261121T06.jsonl.gz",
        [
            _position_line("2026-11-21 06:00:05.000000000 +0000 UTC"),
            _position_line("2026-11-21 06:00:42.000000000 +0000 UTC"),
            _position_line("2026-11-21 06:00:58.000000000 +0000 UTC"),
            _position_line("2026-11-21 06:02:10.000000000 +0000 UTC"),
        ],
    )
    args = _args(directory)

    tracks, n_pings, coverage, source = validate_tracks._load(args)

    assert n_pings == 4
    assert {row.minute: row.n_msgs for row in coverage} == {
        datetime(2026, 11, 21, 6, 0, tzinfo=UTC): 3,
        datetime(2026, 11, 21, 6, 1, tzinfo=UTC): 0,
        datetime(2026, 11, 21, 6, 2, tzinfo=UTC): 1,
    }
    assert tracks and tracks[0].pings
    # The report is committed, so only the repo-relative path may appear.
    assert source == "aisstream files in data/cache/ais"
    assert str(tmp_path) not in source

    detection = validate_tracks._detection(args, tracks, coverage, np.random.default_rng(args.seed))

    assert detection["status"] == "ok"


def test_jsonl_mode_reads_plain_jsonl_too(tmp_path) -> None:
    path = tmp_path / "20261121T06.jsonl"
    path.write_text(_position_line("2026-11-21 06:00:05 +0000 UTC") + "\n", encoding="utf-8")

    pings, coverage = validate_tracks._load_jsonl(tmp_path)

    assert len(pings) == 1
    assert [(row.minute, row.n_msgs) for row in coverage] == [(datetime(2026, 11, 21, 6, 0, tzinfo=UTC), 1)]


def test_display_path_is_relative_inside_the_repo_and_short_outside(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(validate_tracks, "ROOT", tmp_path / "repo")

    inside = tmp_path / "repo" / "data" / "ais"
    outside = tmp_path / "home" / "someone" / "recordings"

    assert validate_tracks._display_path(inside) == "data/ais"
    assert validate_tracks._display_path(outside) == "someone/recordings"
