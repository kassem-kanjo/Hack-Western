"""Prove scripts/pitch_numbers.py turns a snapshot into the sheet the pitch reads.

The fixture is deliberately tiny: only the fields the sheet reads, in the shapes the
builders write. That keeps the test about the extraction rules (rounding like the UI,
alert categories, fallbacks) rather than about any real recording.

Run: ~/.local/bin/uv run pytest -q tests/test_numbers.py
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pitch_numbers.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("pitch_numbers", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


numbers = _load_script()


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _live(region: str, *, vessels: int, start: str, end: str, spoof: bool = False) -> dict:
    events = []
    if spoof:
        events.append(
            {
                "kind": "spoof",
                "severity": 0.95,
                "mmsi": 630001188,
                "start": start,
                "evidence": {
                    "n_vessels": 4,
                    "vessels": [
                        {"offset_km": 40.0},
                        {"offset_km": 51.2},
                        {"offset_km": 44.0},
                        {"offset_km": 47.5},
                    ],
                },
            }
        )
    return {
        "region": region,
        "window": {"start": start, "end": end},
        "counts": {
            "vessels": vessels,
            "silences": 12,
            "jumps": 3,
            "clones": 1,
            "meetings": 2,
            "spoofs": 1 if spoof else 0,
        },
        "events": events,
        "watchlist": {
            "alerts": [
                {"mmsi": 1, "category": "sanctioned"},
                {"mmsi": 2, "category": "shadow_fleet"},
                {"mmsi": 3, "category": "sanctioned"},
            ],
            "info": [{"mmsi": 4, "category": "detention"}],
        },
    }


def _fixture(snapshot: Path) -> Path:
    _write(
        snapshot / "hormuz_unseen.json",
        {
            "region": "HORMUZ",
            "period": {"start": "2026-08-15", "end": "2026-11-15"},
            "daily": [],
            "summary": {
                "days": 92,
                "n_days_used": 59,
                "headline_share": 0.4123,
                "dark_share_mean": 0.4001,
            },
        },
    )
    _write(
        snapshot / "cases.json",
        {
            "totals": {
                "gaps_scanned": 5624,
                "with_candidates": 109,
                "candidates_final": 19,
                "ruled_out": 90,
            },
            "cases": [{"status": "candidate"}] * 19,
        },
    )
    _write(
        snapshot / "stats.json",
        {
            "dark_hours_daily": [],
            "sar_weekly": [],
            "ais_hourly": [{"hour": "2026-11-21T08:00:00Z", "msgs": 12}],
            "source": "tiger",
            "validation": {"null_ratio": 1.07, "bridge_auc": 0.87},
        },
    )
    _write(
        snapshot / "live_BALTIC_WATCH.json",
        _live(
            "BALTIC_WATCH",
            vessels=3751,
            start="2026-11-21T00:00:00Z",
            end="2026-11-21T09:00:00Z",
            spoof=True,
        ),
    )
    _write(
        snapshot / "live_MALAYSIA_EOPL.json",
        _live("MALAYSIA_EOPL", vessels=310, start="2026-11-21T00:00:00Z", end="2026-11-21T02:30:00Z"),
    )
    _write(
        snapshot / "live_BLACK_SEA.json",
        _live("BLACK_SEA", vessels=100, start="2026-11-21T00:00:00Z", end="2026-11-21T01:00:00Z"),
    )
    return _write(
        snapshot.parent / "validation" / "tracks_results.json",
        {
            "status": "ok",
            "containment": {"n": 66, "inside_lens_at_truth_time": 62, "rate": 0.9394},
            "bridge": {"auc": 0.871},
            "detection": {
                "status": "ok",
                "found_underway": 55,
                "injected_underway": 55,
                "recall_underway": 1.0,
            },
            "false_positive": {
                "status": "ok",
                "events": 1,
                "vessel_days": 87.1,
                "per_vessel_day": 0.0115,
            },
        },
    )


def test_numbers_from_minimal_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    validation = _fixture(snapshot)
    out_md = tmp_path / "out" / "numbers.md"
    out_json = tmp_path / "out" / "numbers.json"

    code = numbers.main(
        [
            "--snapshot-dir",
            str(snapshot),
            "--validation",
            str(validation),
            "--out-md",
            str(out_md),
            "--out-json",
            str(out_json),
        ]
    )

    assert code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    values = payload["values"]
    assert payload["missing"] == []

    assert values["hormuz.share_pct"] == 41
    assert values["hormuz.days_used"] == 59
    assert values["hormuz.days_total"] == 92
    assert values["hormuz.date_range"] == "2026-08-15 to 2026-11-15"

    assert values["live.BALTIC_WATCH.vessels"] == 3751
    assert values["live.BALTIC_WATCH.window_hours"] == 9.0
    assert values["live.BALTIC_WATCH.window_h"] == 9
    assert values["live.BALTIC_WATCH.events.silence"] == 12
    assert values["live.BALTIC_WATCH.events.spoof"] == 1
    assert values["live.BALTIC_WATCH.watchlist_alerts"] == 3
    assert values["live.MALAYSIA_EOPL.window_hours"] == 2.5

    assert values["spoof.ships"] == 4
    assert values["spoof.offset_km_min"] == 40.0
    assert values["spoof.offset_km_max"] == 51.2

    assert values["funnel.gaps_scanned"] == 5624
    assert values["funnel.with_candidates"] == 109
    assert values["funnel.unexplained"] == 19
    assert values["funnel.ruled_out"] == 90

    assert values["validation.containment_n"] == 62
    assert values["validation.containment_total"] == 66
    assert values["validation.containment_pct"] == 93.9
    assert values["validation.false_positive_events"] == 1
    assert values["validation.false_positive_vessel_days"] == 87.1
    assert values["validation.recall_found"] == 55
    assert values["validation.recall_injected"] == 55
    assert values["validation.recall_pct"] == 100.0

    assert values["stats.source"] == "tiger"
    assert values["stats.null_ratio"] == 1.07
    assert values["stats.bridge_auc"] == 0.87

    markdown = out_md.read_text(encoding="utf-8")
    assert "Hormuz share" in markdown
    assert "`summary.headline_share`" in markdown
    assert "Spoof event ships" in markdown
    assert "`containment.rate`" in markdown


def test_missing_sources_are_reported(tmp_path: Path) -> None:
    code = numbers.main(
        [
            "--snapshot-dir",
            str(tmp_path / "empty"),
            "--validation",
            str(tmp_path / "nope.json"),
            "--out-md",
            str(tmp_path / "numbers.md"),
            "--out-json",
            str(tmp_path / "numbers.json"),
        ]
    )

    assert code == 1
    payload = json.loads((tmp_path / "numbers.json").read_text(encoding="utf-8"))
    assert len(payload["missing"]) == 7
    assert "## Missing sources" in (tmp_path / "numbers.md").read_text(encoding="utf-8")


def test_merge_stats_keeps_other_keys(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    target = _write(
        snapshot / "stats.json",
        {
            "dark_hours_daily": [{"day": "2026-11-20", "hours": 3.0, "gaps": 2}],
            "validation": {"null_ratio": 1.07},
            "source": "sqlite",
        },
    )
    fresh = _write(
        tmp_path / "fresh.json",
        {
            "dark_hours_daily": [{"day": "2026-11-21", "hours": 1.0, "gaps": 0}],
            "ais_hourly": [{"hour": "2026-11-21T09:00:00Z", "msgs": 5}],
            "sar_weekly": [],
            "source": "tiger",
            "validation": None,
        },
    )

    assert numbers.merge_stats(fresh, target) == 0
    merged = json.loads(target.read_text(encoding="utf-8"))

    assert merged["source"] == "tiger"
    assert merged["ais_hourly"] == [{"hour": "2026-11-21T09:00:00Z", "msgs": 5}]
    assert merged["sar_weekly"] == []
    assert merged["dark_hours_daily"] == [{"day": "2026-11-20", "hours": 3.0, "gaps": 2}]
    assert merged["validation"] == {"null_ratio": 1.07}
