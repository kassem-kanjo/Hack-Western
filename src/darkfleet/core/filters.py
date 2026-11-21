"""Filters that remove cases which are not real dark-vessel encounters.

Everything here is pure: no file, network or clock I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Protocol

from darkfleet.core.geo import Fix, distance_km

Gap = tuple[Fix, Fix, float]  # (off fix, on fix, speed cap knots); same shape as core/match.py


def is_stationary(gap: Gap, km: float = 5.0) -> bool:
    """True if the off and on fixes are within `km` kilometres (the ship did not really move: not a case)."""
    off, on, _ = gap
    return distance_km(off.lat, off.lon, on.lat, on.lon) <= km


def recurring_cells(
    cells: Iterable[tuple[float, float, date]], min_days: int = 3
) -> set[tuple[float, float]]:
    """Cells (lat, lon) that carry a radar detection on at least `min_days` DISTINCT days.

    Such cells are fixed structures (platforms, moorings), not ships. Keys are used exactly as
    given.
    """
    days: dict[tuple[float, float], set[date]] = {}
    for lat, lon, day in cells:
        days.setdefault((lat, lon), set()).add(day)
    return {key for key, seen in days.items() if len(seen) >= min_days}


class HasSlack(Protocol):
    slack: float


def meeting_ok(window: HasSlack, min_slack: float = 0.3) -> bool:
    """A meeting is shown only if its slack is strictly greater than `min_slack`."""
    return window.slack > min_slack
