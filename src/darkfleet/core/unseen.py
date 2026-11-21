"""The unseen fleet: how many radar detections lacked a matching AIS broadcast.

Everything here is a pure aggregation over the SAR cell rows returned by
``GfwClient.sar_cells``. We deliberately never name a vessel: a missing AIS match can be
a receiver gap, a ship under the radar size floor, or a fishing boat outside its
licence, so only fleet-level counts leave this module.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from datetime import date
from typing import TypedDict

from darkfleet.ingest.gfw import SarCellRow

__all__ = ["DailyCount", "GridCell", "Summary", "daily_counts", "density_grid", "summary"]

# Floor needs a small tolerance: 25.1 / 0.1 is 250.99999999999997 in binary floating point
# and would otherwise land in the cell below the one the coordinate belongs to.
_INDEX_EPS = 1e-9


class DailyCount(TypedDict):
    """One UTC day of the fleet-wide radar series.

    ``seen`` is every radar detection, ``broadcasting`` those matched to an AIS
    broadcast and ``dark`` the rest, so ``seen == broadcasting + dark`` always holds.
    """

    day: str
    seen: int
    broadcasting: int
    dark: int
    dark_share: float


class GridCell(TypedDict):
    """One density cell: its centre and the detection count that fell inside it."""

    lat: float
    lon: float
    n: int


class Summary(TypedDict):
    """The headline numbers for a window and their uncertainty across days."""

    days: int
    seen_total: int
    dark_total: int
    dark_share_mean: float
    dark_share_ci: list[float]
    busiest_day: str | None


def _accumulate(totals: dict[date, list[int]], rows: Iterable[SarCellRow], slot: int) -> None:
    """Add ``detections`` to one side of the ledger for every row's UTC day."""
    for row in rows:
        bucket = totals.setdefault(row.day, [0, 0])
        bucket[slot] += row.detections


def daily_counts(
    matched_rows: Iterable[SarCellRow],
    unmatched_rows: Iterable[SarCellRow],
) -> list[DailyCount]:
    """Per-day radar counts split into matched (broadcasting) and unmatched (dark).

    ``seen`` is the sum of the ``detections`` column, not the number of cells: one cell
    can hold several radar contacts. Days present in only one series are still emitted,
    with the missing side at zero, and the result is sorted by ISO day so the snapshot
    is byte-stable.
    """
    totals: dict[date, list[int]] = {}
    _accumulate(totals, matched_rows, 0)  # broadcasting
    _accumulate(totals, unmatched_rows, 1)  # dark

    daily: list[DailyCount] = []
    for day in sorted(totals):
        broadcasting, dark = totals[day]
        seen = broadcasting + dark
        daily.append(
            DailyCount(
                day=day.isoformat(),
                seen=seen,
                broadcasting=broadcasting,
                dark=dark,
                dark_share=(dark / seen) if seen else 0.0,
            )
        )
    return daily


def density_grid(unmatched_rows: Iterable[SarCellRow], cell_deg: float = 0.1) -> list[GridCell]:
    """Bucket unmatched detections into ``cell_deg`` squares and return cell centres.

    ``n`` sums ``detections`` per cell, because the point of the map is where unseen
    radar contacts concentrate, not how many cells happened to contain one. Cells are
    keyed on the rounded grid index so the centre is stable at 6 decimals for JSON.
    """
    if cell_deg <= 0:
        raise ValueError("cell_deg must be positive")

    counts: dict[tuple[int, int], int] = {}
    for row in unmatched_rows:
        lat_index = math.floor(row.lat / cell_deg + _INDEX_EPS)
        lon_index = math.floor(row.lon / cell_deg + _INDEX_EPS)
        key = (lat_index, lon_index)
        counts[key] = counts.get(key, 0) + row.detections

    grid = [
        GridCell(
            lat=round((lat_index + 0.5) * cell_deg, 6),
            lon=round((lon_index + 0.5) * cell_deg, 6),
            n=n,
        )
        for (lat_index, lon_index), n in counts.items()
    ]
    grid.sort(key=lambda cell: (cell["lat"], cell["lon"]))
    return grid


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted sample."""
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _bootstrap_ci(
    values: Sequence[float],
    *,
    resamples: int,
    seed: int,
) -> list[float]:
    """95% percentile interval of the mean, resampling days with replacement.

    Days, not detections, are the independent unit: a day with a busy strait contributes
    one observation however many cells fired. Seeding makes the snapshot reproducible.
    """
    if not values:
        return [0.0, 0.0]
    if len(values) == 1:
        return [values[0], values[0]]

    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        sample = rng.choices(values, k=len(values))
        means.append(sum(sample) / len(sample))
    means.sort()
    return [_percentile(means, 0.025), _percentile(means, 0.975)]


def summary(
    daily: Sequence[DailyCount],
    *,
    resamples: int = 2000,
    seed: int = 7,
) -> Summary:
    """Headline totals plus a bootstrap interval for the mean daily dark share.

    A tie on ``seen`` goes to the earliest day so the answer does not depend on the
    input order. An empty window returns zeros and a null busiest day instead of
    raising: a snapshot for a region with no radar passes should still be a valid file.
    """
    shares = [float(row["dark_share"]) for row in daily]
    busiest_day: str | None = None
    if daily:
        busiest_seen = max(row["seen"] for row in daily)
        busiest_day = min(row["day"] for row in daily if row["seen"] == busiest_seen)

    return Summary(
        days=len(daily),
        seen_total=sum(row["seen"] for row in daily),
        dark_total=sum(row["dark"] for row in daily),
        dark_share_mean=(sum(shares) / len(shares)) if shares else 0.0,
        dark_share_ci=_bootstrap_ci(shares, resamples=resamples, seed=seed),
        busiest_day=busiest_day,
    )
