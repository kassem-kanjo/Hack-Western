"""Maximum plausible vessel speeds.

ASSUMED SERVICE SPEEDS, NO PRIMARY SOURCE FOUND. docs/research.md section 5 gives no sourced
figure for these classes, so every speed in the table below is our own working assumption
(issue #28), not a measured or published value. Each is multiplied by the 1.15 margin so the
prism errs towards "could have been there"; a too-tight bound would hide real candidates.
"""

from __future__ import annotations

MARGIN = 1.15
_TABLE_KNOTS = {
    "cargo": 14.0,
    "bulk": 14.0,
    "tanker": 15.0,
    "fishing": 12.0,
    "carrier": 15.0,
    "passenger": 20.0,
    "hsc": 42.0,
    "service": 30.0,
    "other_non_fishing": 14.0,
}
_DEFAULT_KNOTS = 14.0


def max_speed_knots(vessel_type: str | None) -> float:
    """Upper speed bound (knots) for a vessel class, margin included."""
    key = (vessel_type or "").strip().lower()
    return _TABLE_KNOTS.get(key, _DEFAULT_KNOTS) * MARGIN
