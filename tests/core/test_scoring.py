"""Prove the analyst-attention score is transparent, bounded and hard to over-read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from darkfleet.core.scoring import SCORE_NOTE, score_case

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "api" / "case.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _points(result: dict[str, Any]) -> int:
    return sum(factor["points"] for factor in result["score_factors"])


def test_fixture_case_is_bounded_and_factors_add_up() -> None:
    case = _fixture()

    result = score_case(case)

    assert 0 <= result["score"] <= 100
    assert result["band"] in {"low", "medium", "high"}
    keys = {factor["key"] for factor in result["score_factors"]}
    assert {"gap_hours", "detour", "radar_at_scene"} <= keys
    # The cap at 100 may make the positive factors sum to more than the returned score.
    if result["score"] < 100:
        assert _points(result) == result["score"]
    else:
        assert _points(result) >= 100
    assert result["score_note"] == SCORE_NOTE
    for factor in result["score_factors"]:
        assert set(factor) == {"key", "label", "value", "points", "note"}
        assert isinstance(factor["points"], int)
        assert factor["note"]


def test_ruled_out_case_is_capped_at_20_with_a_negative_factor() -> None:
    case = _fixture()
    case["status"] = "ruled_out"

    result = score_case(case)

    assert result["score"] <= 20
    penalty = next(factor for factor in result["score_factors"] if factor["key"] == "status_ruled_out")
    assert penalty["points"] < 0
    assert _points(result) == result["score"]


def test_ruled_out_below_the_cap_keeps_its_score() -> None:
    result = score_case({"status": "ruled_out", "hours": 10, "candidates": []})

    assert result["score"] == 2
    assert _points(result) == result["score"]


def test_sanctions_match_adds_40_points() -> None:
    base = {"hours": 10, "candidates": []}

    without = score_case(base)
    with_match = score_case({**base, "sanctions": {"match": True}})

    assert with_match["score"] == without["score"] + 40
    sanctions = next(factor for factor in with_match["score_factors"] if factor["key"] == "sanctions")
    assert sanctions["points"] == 40
    assert "needs analyst confirmation" in sanctions["note"]


def test_band_thresholds_at_39_40_69_70() -> None:
    cases = [
        # 20 (hours) + 9 (implied speed) + 10 (intentional) = 39
        ({"hours": 96, "straight_line_kn": 10, "speed_cap_kn": 18, "intentional_disabling": True}, 39, "low"),
        ({"sanctions": {"match": True}}, 40, "medium"),
        # 20 (hours) + 9 (implied speed) + 40 (sanctions) = 69
        (
            {"hours": 96, "straight_line_kn": 10, "speed_cap_kn": 18, "sanctions": {"match": True}},
            69,
            "medium",
        ),
        # 20 (hours) + 40 (sanctions) + 10 (intentional) = 70
        ({"hours": 96, "sanctions": {"match": True}, "intentional_disabling": True}, 70, "high"),
    ]

    for case, expected_score, expected_band in cases:
        result = score_case(case)
        assert result["score"] == expected_score
        assert result["band"] == expected_band
        assert _points(result) == expected_score
