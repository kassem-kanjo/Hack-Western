"""Prove scripts/build_snapshot.py takes the funnel totals from the re-rank counts table."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_snapshot.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_snapshot", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_snapshot = _load_script()

GAPS_SCANNED = 5624

CASES: list[dict[str, Any]] = [
    {"id": "gap:a", "status": "candidate", "n_candidates": 2, "n_meetings": 0},
    {"id": "gap:b", "status": "candidate", "n_candidates": 1, "n_meetings": 1},
    {"id": "gap:c", "status": "ruled_out", "n_candidates": 1, "n_meetings": 0},
    {"id": "gap:d", "status": "plain", "n_candidates": 0, "n_meetings": 0},
]

# A deliberately small baseline (7), so a test cannot pass by counting statuses (3).
COUNTS: dict[str, Any] = {
    "baseline": {"gaps_with_candidates": 7, "total_candidates": 42},
    "stage_a_stationary": {"gaps_left": 5},
    "final": {"candidate": 2, "ruled_out": 1},
}


def _write_inputs(tmp_path: Path, counts: dict[str, Any] | None) -> tuple[Path, Path, Path | None]:
    source = tmp_path / "cases_v2.json"
    source.write_text(
        json.dumps({"generated_at": "2026-11-21T06:00:00Z", "cases": CASES}),
        encoding="utf-8",
    )
    counts_path = None
    if counts is not None:
        counts_path = tmp_path / "rerank_counts.json"
        counts_path.write_text(json.dumps(counts), encoding="utf-8")
    return source, tmp_path / "snapshot", counts_path


def _built_totals(out: Path, totals: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    assert payload["totals"] == totals
    return payload["totals"]


def test_funnel_comes_from_the_counts_file(tmp_path: Path) -> None:
    source, out, counts_path = _write_inputs(tmp_path, COUNTS)

    totals = build_snapshot.build(source, out, GAPS_SCANNED, counts_path)

    assert totals == {
        "gaps_scanned": GAPS_SCANNED,
        "with_candidates": COUNTS["baseline"]["gaps_with_candidates"],
        "ruled_out": 1,
        "candidates_final": 2,
        "meetings": 1,
        "funnel": [
            {"stage": "scanned", "label": "Gaps scanned", "count": GAPS_SCANNED},
            {"stage": "radar", "label": "Radar inside prism", "count": 7},
            {"stage": "unexplained", "label": "Unexplained after physics checks", "count": 2},
        ],
    }
    assert _built_totals(out, totals) == totals


def test_missing_counts_file_falls_back_to_status_totals(tmp_path: Path) -> None:
    source, out, _ = _write_inputs(tmp_path, None)

    totals = build_snapshot.build(source, out, GAPS_SCANNED, tmp_path / "absent.json")

    assert totals == {
        "gaps_scanned": GAPS_SCANNED,
        "with_candidates": 3,  # both candidates plus the ruled-out case carrying a candidate
        "ruled_out": 1,
        "meetings": 1,
    }
    assert "funnel" not in totals
    assert "candidates_final" not in totals
    assert _built_totals(out, totals) == totals


def test_counts_defaults_to_the_legacy_totals_without_a_path(tmp_path: Path) -> None:
    source, out, _ = _write_inputs(tmp_path, None)

    totals = build_snapshot.build(source, out, GAPS_SCANNED, None)

    assert set(totals) == {"gaps_scanned", "with_candidates", "ruled_out", "meetings"}
