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


RESULTS: dict[str, Any] = {
    "null_test": {
        "variants": {
            "primary": {"ratio": 1.07, "ratio_lo": 0.91, "ratio_hi": 1.25, "p_value": 0.235},
            "filtered": {"ratio": 9.99, "ratio_lo": 9.0, "ratio_hi": 10.0, "p_value": 0.01},
        }
    }
}
BRIDGE: dict[str, Any] = {
    "sigma_km_per_sqrt_h": 21.62,
    "c_exceed": {"ratio": 1.16},
    "d_auc": {"auc": 0.87, "lo": 0.844, "hi": 0.896},
}
HOLDOUT: dict[str, Any] = {"primary": {"ratio": 0.70, "ratio_lo": 0.15, "ratio_hi": 1.37}}


def _write_validation(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("results.json", RESULTS),
        ("bridge_results.json", BRIDGE),
        ("holdout_results.json", HOLDOUT),
    ):
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")
    return directory


CASE_WITH_FRAMES: dict[str, Any] = {
    "id": "gap:with-frames",
    "status": "candidate",
    "n_candidates": 1,
    "n_meetings": 0,
    "off": {"t": "2026-10-07T00:00:00Z", "pos": [56.0, 24.0]},
    "on": {"t": "2026-10-08T00:00:00Z", "pos": [57.0, 24.0]},
    "speed_cap_kn": 20.0,
    "lens_frames": [
        {"t": "2026-10-07T00:00:00Z", "frac": 0.0, "polygon": None},
        {"t": "2026-10-07T12:00:00Z", "frac": 0.5, "polygon": None},
        {"t": "not-a-time", "frac": 0.75, "polygon": None},
    ],
}
CASE_WITHOUT_FRAMES: dict[str, Any] = {
    "id": "gap:no-frames",
    "status": "plain",
    "n_candidates": 0,
    "n_meetings": 0,
}


def _write_cases(tmp_path: Path, cases: list[dict[str, Any]]) -> tuple[Path, Path]:
    source = tmp_path / "cases_v2.json"
    source.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return source, tmp_path / "snapshot"


def _stats(out: Path) -> dict[str, Any]:
    return json.loads((out / "stats.json").read_text(encoding="utf-8"))


def _case(out: Path, case_id: str) -> dict[str, Any]:
    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    return next(case for case in payload["cases"] if case["id"] == case_id)


def test_likely_contours_are_added_to_every_lens_frame(tmp_path: Path) -> None:
    source, out = _write_cases(tmp_path, [CASE_WITH_FRAMES, CASE_WITHOUT_FRAMES])

    build_snapshot.build(
        source, out, GAPS_SCANNED, None, validation_dir=_write_validation(tmp_path / "validation")
    )

    frames = _case(out, "gap:with-frames")["lens_frames"]
    assert len(frames) == 3
    for frame in frames:
        assert "likely" in frame
    assert frames[0]["likely"] is None or frames[0]["likely"]["type"] == "Polygon"
    assert frames[1]["likely"]["type"] == "Polygon"
    ring = frames[1]["likely"]["coordinates"][0]
    assert len(ring) == 65  # 64 geodesic vertices, closed
    assert ring[0] == ring[-1]
    assert frames[2]["likely"] is None  # unparseable frame time degrades to null

    without = _case(out, "gap:no-frames")
    assert "lens_frames" not in without


def test_validation_stats_come_from_the_result_files(tmp_path: Path) -> None:
    source, out = _write_cases(tmp_path, [CASE_WITHOUT_FRAMES])

    build_snapshot.build(
        source, out, GAPS_SCANNED, None, validation_dir=_write_validation(tmp_path / "validation")
    )

    validation = _stats(out)["validation"]
    assert validation["null_ratio"] == RESULTS["null_test"]["variants"]["primary"]["ratio"]
    assert validation["null_ci"] == [0.91, 1.25]  # primary variant, not "filtered"
    assert validation["null_p"] == 0.235
    assert validation["bridge_auc"] == 0.87
    assert validation["bridge_auc_ci"] == [0.844, 0.896]
    assert validation["bridge_radar_ratio"] == 1.16
    assert validation["holdout_ratio"] == 0.70
    assert validation["holdout_ci"] == [0.15, 1.37]
    assert validation["verdict"].startswith("Radar near dark ships")
    assert validation["source"] == "docs/validation.md"


def test_missing_validation_files_give_nulls(tmp_path: Path) -> None:
    source, out = _write_cases(tmp_path, [CASE_WITH_FRAMES])

    build_snapshot.build(source, out, GAPS_SCANNED, None, validation_dir=tmp_path / "absent-validation")

    validation = _stats(out)["validation"]
    assert validation["null_ratio"] is None
    assert validation["null_ci"] is None
    assert validation["null_p"] is None
    assert validation["bridge_auc"] is None
    assert validation["bridge_auc_ci"] is None
    assert validation["bridge_radar_ratio"] is None
    assert validation["holdout_ratio"] is None
    assert validation["holdout_ci"] is None
    assert validation["verdict"]
    assert validation["source"] == "docs/validation.md"

    # the contours still get drawn at the documented fallback sigma
    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    assert payload["cases"][0]["lens_frames"][1]["likely"]["type"] == "Polygon"


def test_snapshot_files_are_written_through_tmp_files(tmp_path: Path, monkeypatch) -> None:
    source, out, _ = _write_inputs(tmp_path, None)
    calls: list[tuple[Path, Path]] = []
    real_replace = build_snapshot.os.replace

    def record(src, dst):
        calls.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(build_snapshot.os, "replace", record)

    build_snapshot.build(source, out, GAPS_SCANNED, None, validation_dir=tmp_path / "absent-validation")

    assert calls == [
        (out / "cases.tmp", out / "cases.json"),
        (out / "stats.tmp", out / "stats.json"),
    ]
    assert not (out / "cases.tmp").exists()
    assert not (out / "stats.tmp").exists()
