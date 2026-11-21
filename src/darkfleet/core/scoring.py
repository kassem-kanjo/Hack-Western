"""Explainable analyst-attention score for a dark-gap case.

The score ranks cases for a human to look at first. It is deliberately simple and
transparent: every factor carries its own points and a plain-English note, and the
points add up to the score. Nothing here is evidence of wrongdoing (see
``docs/validation.md`` for why radar near a dark ship is weak evidence on its own).

Pure module: no file, network or clock I/O.
"""

from __future__ import annotations

from typing import Any

SCORE_NOTE = "Ranks cases for analyst attention. It is not evidence of wrongdoing."


def _band(score: int) -> str:
    """Map a 0-100 score to the UI band (low < 40 <= medium < 70 <= high)."""
    if score < 40:
        return "low"
    if score < 70:
        return "medium"
    return "high"


def _factor(key: str, label: str, value: str, points: int, note: str) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "points": points, "note": note}


def score_case(case: dict[str, Any]) -> dict[str, Any]:
    """Score one case for analyst attention.

    Returns ``score`` (int 0-100), ``band`` (low/medium/high), ``score_factors`` (each
    with key, label, value, points and note) and the constant ``score_note`` reminder
    that the score is not evidence. A factor is included only when its input is present.
    """
    factors: list[dict[str, Any]] = []

    hours = case.get("hours")
    if hours is not None:
        points = min(20, round(hours / 96 * 20))
        factors.append(
            _factor(
                "gap_hours",
                "Gap length",
                f"{hours:g} h",
                points,
                "longer silences leave more unexplained time",
            )
        )

    straight_line_kn = case.get("straight_line_kn")
    speed_cap_kn = case.get("speed_cap_kn")
    if straight_line_kn and speed_cap_kn:
        ratio = straight_line_kn / speed_cap_kn
        points = max(0, min(20, round((1 - ratio) * 20)))
        factors.append(
            _factor(
                "detour",
                "Implied speed",
                f"{straight_line_kn:g} of {speed_cap_kn:g} kn",
                points,
                "low implied speed = time unaccounted for (loitering or detour)",
            )
        )

    candidates = case.get("candidates")
    if isinstance(candidates, list):
        n = sum(
            1
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("verdict") == "candidate"
        )
        points = min(10, 2 * n)
        factors.append(
            _factor(
                "radar_at_scene",
                "Radar at scene",
                f"{n} candidate detections",
                points,
                "weak evidence: radar near dark ships matches chance in our tests (docs/validation.md)",
            )
        )

    sanctions = case.get("sanctions")
    if sanctions and sanctions.get("match"):
        factors.append(
            _factor(
                "sanctions",
                "Sanctions match",
                "possible match",
                40,
                "possible list match, needs analyst confirmation",
            )
        )

    if case.get("intentional_disabling") is True:
        factors.append(
            _factor(
                "intentional_disabling",
                "Intentional disabling",
                "flagged in gap event",
                10,
                "GFW flagged the gap as likely intentional",
            )
        )

    total = sum(factor["points"] for factor in factors)
    if case.get("status") == "ruled_out":
        capped = min(total, 20)
        value = f"would score {total}, capped at 20" if total > 20 else "ruled out after checks"
        factors.append(
            _factor(
                "status_ruled_out",
                "Case ruled out",
                value,
                capped - total,
                "a check ruled this case out, so it ranks no higher than 20",
            )
        )
        total = capped

    score = max(0, min(100, total))
    return {
        "score": score,
        "band": _band(score),
        "score_factors": factors,
        "score_note": SCORE_NOTE,
    }
