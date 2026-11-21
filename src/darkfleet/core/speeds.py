"""Maximum plausible vessel speeds.

Source: docs/research.md (typical service speeds by vessel class). We add a 15% margin so the
prism errs towards "could have been there"; a too-tight bound would hide real candidates.
"""

from __future__ import annotations

MARGIN = 1.15
_TABLE_KNOTS = {
    "tanker": 17.0,
    "cargo": 17.0,
    "carrier": 17.0,
    "other_non_fishing": 17.0,
    "fishing": 14.0,
    "passenger": 25.0,
}
_DEFAULT_KNOTS = 18.0


def max_speed_knots(vessel_type: str | None) -> float:
    """Upper speed bound (knots) for a vessel class, margin included."""
    key = (vessel_type or "").strip().lower()
    return _TABLE_KNOTS.get(key, _DEFAULT_KNOTS) * MARGIN
