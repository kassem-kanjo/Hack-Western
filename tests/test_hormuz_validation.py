"""Offline tests for scripts/hormuz_validation.py using tiny daily-series fixtures."""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hormuz_validation.py"
START = date(2026, 1, 1)


def _load_script():
    spec = importlib.util.spec_from_file_location("hormuz_validation", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hormuz_validation = _load_script()


def _series(dark_by_day: list[int], seen: int = 100) -> list[dict]:
    return [
        {
            "day": (START + timedelta(days=index)).isoformat(),
            "seen": seen,
            "broadcasting": seen - dark,
            "dark": dark,
            "dark_share": dark / seen,
        }
        for index, dark in enumerate(dark_by_day)
    ]


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _inputs(tmp_path: Path, *, seen: int = 100) -> tuple[Path, Path, Path]:
    # 90 days from 2026-01-01. The treated series steps up on 2026-02-10 (day 40), the
    # control stays flat, so the DiD should be clearly positive.
    treated = _series([min(5, seen)] * 40 + [min(55, seen)] * 50, seen=seen)
    control = _series([min(10, seen)] * 90, seen=seen)
    events = [
        {
            "date_start": "2026-02-10",
            "date_end": "2026-02-12",
            "type": "shipping",
            "title": "Example event",
            "source_url": "https://example.test/event",
            "confidence": "high",
        }
    ]
    return (
        _write(tmp_path / "treated.json", {"region": "HORMUZ", "daily": treated}),
        _write(
            tmp_path / "control.json",
            {"region": "GULF_OF_OMAN_CONTROL", "daily": control},
        ),
        _write(tmp_path / "events.json", events),
    )


def _args(tmp_path: Path, treated: Path, control: Path, events: Path) -> list[str]:
    return [
        "--treated",
        str(treated),
        "--control",
        str(control),
        "--events",
        str(events),
        "--out",
        str(tmp_path / "validation.json"),
        "--md",
        str(tmp_path / "validation.md"),
        "--baseline-start",
        "2026-01-01",
        "--baseline-end",
        "2026-01-20",
        "--current-start",
        "2026-02-20",
        "--current-end",
        "2026-03-10",
        "--n-boot",
        "200",
        "--seed",
        "4",
    ]


def test_main_writes_report_and_json(tmp_path: Path) -> None:
    treated, control, events = _inputs(tmp_path)

    assert hormuz_validation.main(_args(tmp_path, treated, control, events)) == 0

    payload = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert payload["regions"] == {
        "treated": "HORMUZ",
        "control": "GULF_OF_OMAN_CONTROL",
    }
    assert payload["window_days"] == 14
    assert payload["min_seen"] == 20
    assert payload["n_boot"] == 200
    assert payload["seed"] == 4
    assert payload["source"].startswith("Global Fishing Watch")
    assert "Coincides with" in payload["caveats"][2]

    event = payload["events"][0]
    assert event["title"] == "Example event"
    assert event["type"] == "shipping"
    assert event["source_url"] == "https://example.test/event"
    assert event["confidence"] == "high"
    assert event["treated"]["pre"]["dark_share"] == 0.05
    assert event["treated"]["post"]["dark_share"] == 0.55
    assert event["control"]["pre"]["dark_share"] == 0.10
    assert event["control"]["post"]["dark_share"] == 0.10
    assert event["did"] == pytest.approx(0.50)
    assert event["verdict"] == "moves with event"
    assert event["did_ci"][0] <= event["did"] <= event["did_ci"][1]

    regime = payload["regime"]
    assert regime["did"] == pytest.approx(0.50)
    assert regime["verdict"] == "moves with event"

    assert {row["month"] for row in payload["coverage"]["treated"]} == {
        "2026-01",
        "2026-02",
        "2026-03",
    }
    assert payload["coverage"]["treated"][0]["mean_seen_per_day"] == pytest.approx(100.0)

    report = (tmp_path / "validation.md").read_text(encoding="utf-8")
    assert "# Hormuz radar validation" in report
    assert "| Date | Event | Hormuz pre → post | Control pre → post | DiD (95% CI) | Verdict |" in report
    assert "Example event" in report
    assert "moves with event" in report
    assert "## Coverage" in report
    assert "days used" in report
    assert "Coincides with" in report
    assert "not a causal estimate" in report


def test_main_reports_insufficient_coverage(tmp_path: Path) -> None:
    treated, control, events = _inputs(tmp_path, seen=5)

    assert hormuz_validation.main(_args(tmp_path, treated, control, events)) == 0

    payload = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert payload["events"][0]["verdict"] == "insufficient coverage"
    assert payload["events"][0]["did"] is None
    assert payload["regime"]["verdict"] == "insufficient coverage"
    report = (tmp_path / "validation.md").read_text(encoding="utf-8")
    assert "insufficient coverage" in report


def test_missing_input_is_a_clean_failure(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    args = _args(tmp_path, missing, missing, missing)

    assert hormuz_validation.main(args) == 2
    assert not (tmp_path / "validation.json").exists()
    assert not (tmp_path / "validation.md").exists()


def test_events_may_be_wrapped_in_an_object(tmp_path: Path) -> None:
    treated, control, events = _inputs(tmp_path)
    wrapped = _write(tmp_path / "wrapped.json", {"events": json.loads(events.read_text(encoding="utf-8"))})

    assert hormuz_validation.main(_args(tmp_path, treated, control, wrapped)) == 0
    payload = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert len(payload["events"]) == 1


def test_empty_events_still_produce_a_report(tmp_path: Path) -> None:
    treated, control, _ = _inputs(tmp_path)
    empty = _write(tmp_path / "empty.json", [])

    assert hormuz_validation.main(_args(tmp_path, treated, control, empty)) == 0
    report = (tmp_path / "validation.md").read_text(encoding="utf-8")
    assert "no events in the events file" in report
    payload = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert payload["events"] == []
