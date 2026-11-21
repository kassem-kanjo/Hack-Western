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

LOW_COVERAGE_FRACTION = 0.2
"""A day with fewer than this share of the median day's detections is a reception gap.

Real GFW windows contain partial first/last days and satellite-pass outages. Those days
have a tiny denominator, so their ``dark_share`` is mostly noise; leaving them in the mean
or median lets one broken day move the fleet headline. Twenty percent is deliberately
generous: the busiest days in a region set the scale, and a genuine quiet day still clears
it unless it is an order of magnitude below the rest.
"""


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
    """The headline numbers for a window and their uncertainty across usable days.

    ``days`` is the raw number of days in the window; ``n_days_used`` and
    ``n_days_excluded`` partition it (low-coverage days are named in ``excluded_days``).
    The spread fields describe the usable days only, because a partial satellite day is a
    measurement gap rather than a quiet sea. ``headline_share`` repeats the median for the
    caller that renders the one number a viewer sees first.
    """

    days: int
    seen_total: int
    dark_total: int
    dark_share_mean: float
    dark_share_ci: list[float]
    median_dark_share: float
    iqr: list[float]
    headline_share: float
    n_days_used: int
    n_days_excluded: int
    excluded_days: list[str]
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


def _usable_days(daily: Sequence[DailyCount]) -> tuple[list[DailyCount], list[str]]:
    """Split the window into days worth averaging and low-coverage days to report.

    The threshold is relative to the median day, not to an absolute detection count, so it
    travels between regions with very different radar traffic. A day exactly at the
    threshold is kept: the rule is "below a fifth", and rounding should not decide a
    boundary case. The excluded labels are sorted so the snapshot does not depend on input
    order.
    """
    if not daily:
        return [], []
    median_seen = _percentile(sorted(float(row["seen"]) for row in daily), 0.5)
    threshold = median_seen * LOW_COVERAGE_FRACTION
    used: list[DailyCount] = []
    excluded: list[str] = []
    for row in daily:
        if float(row["seen"]) < threshold:
            excluded.append(row["day"])
        else:
            used.append(row)
    return used, sorted(excluded)


def summary(
    daily: Sequence[DailyCount],
    *,
    resamples: int = 2000,
    seed: int = 7,
) -> Summary:
    """Headline totals plus robust daily-share statistics over the usable days.

    Low-coverage days (below :data:`LOW_COVERAGE_FRACTION` of the median day) are excluded
    from the share statistics but still counted in ``days``, ``seen_total`` and
    ``dark_total``: the totals describe what the radar returned, while the share and its
    spread describe the days that actually carried enough detections to mean something.
    The median and interquartile range are preferred to the mean because real windows have
    one or two broken days that would otherwise dominate; ``headline_share`` is the median
    so the UI has one explicit field to print.

    A tie on ``seen`` goes to the earliest usable day so the answer does not depend on the
    input order. An empty window returns zeros and a null busiest day instead of raising: a
    snapshot for a region with no radar passes should still be a valid file.
    """
    used, excluded = _usable_days(daily)
    shares = sorted(float(row["dark_share"]) for row in used)
    median_share = _percentile(shares, 0.5)

    busiest_day: str | None = None
    if used:
        busiest_seen = max(row["seen"] for row in used)
        busiest_day = min(row["day"] for row in used if row["seen"] == busiest_seen)

    return Summary(
        days=len(daily),
        seen_total=sum(row["seen"] for row in daily),
        dark_total=sum(row["dark"] for row in daily),
        dark_share_mean=(sum(shares) / len(shares)) if shares else 0.0,
        dark_share_ci=_bootstrap_ci(shares, resamples=resamples, seed=seed),
        median_dark_share=median_share,
        iqr=[_percentile(shares, 0.25), _percentile(shares, 0.75)],
        headline_share=median_share,
        n_days_used=len(used),
        n_days_excluded=len(excluded),
        excluded_days=excluded,
        busiest_day=busiest_day,
    )
