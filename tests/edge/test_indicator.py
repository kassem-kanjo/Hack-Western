"""The ACT LED indicator reads the decision file; no hardware is touched here."""

from __future__ import annotations

import json

from darkfleet.edge.indicator import STALE_S, wanted


def _write(path, level, t):
    path.write_text(json.dumps({"t": t, "alarm": {"level": level}}), encoding="utf-8")


def test_levels_map_to_led_modes(tmp_path):
    f = tmp_path / "d.json"
    for level, mode in (("none", "off"), ("warning", "on"), ("critical", "on"), ("blind", "blink")):
        _write(f, level, 100.0)
        assert wanted(str(f), now=100.5) == mode


def test_stale_or_missing_decision_blinks(tmp_path):
    f = tmp_path / "d.json"
    _write(f, "none", 100.0)
    assert wanted(str(f), now=100.0 + STALE_S + 1) == "blink"
    assert wanted(str(tmp_path / "missing.json")) == "blink"
