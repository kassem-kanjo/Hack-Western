"""Pure event-study helpers: dark share before/after an event, treated area vs control.

Vessel counts arrive as one row per UTC day (``{"day": ..., "seen": n, "dark": k}``). A
window's dark share is the pooled ``sum(dark) / sum(seen)`` over days that clear a
minimum-detections floor, so a partial satellite pass cannot swing the result. The
difference-in-differences subtracts the control area's change from the treated area's
change, and a seeded day-block bootstrap gives its interval. Nothing here touches I/O
and every result is a plain dict so a report or an API can serialise it directly.

These functions describe co-movement. A change that coincides with an event is not
evidence that the event caused it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

DEFAULT_WINDOW_DAYS = 14
DEFAULT_MIN_SEEN = 20
DEFAULT_N_BOOT = 1000
DEFAULT_SEED = 0
DEFAULT_ALPHA = 0.05

VERDICT_MOVES = "moves with event"
VERDICT_NONE = "no detectable change"
VERDICT_INSUFFICIENT = "insufficient coverage"

Row = tuple[date, int, int]  # (day, seen, dark)


def _as_date(value: Any) -> date:
    """Accept a ``date``, a ``datetime`` or an ISO ``YYYY-MM-DD`` string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"expected a date or ISO date string, got {type(value).__name__}")


def _daily_table(series: Sequence[Mapping[str, Any]]) -> dict[date, tuple[int, int]]:
    """Aggregate daily rows by day; duplicate days add together instead of overwriting."""
    table: dict[date, tuple[int, int]] = {}
    for row in series:
        day = _as_date(row["day"])
        seen = int(row["seen"])
        dark = int(row["dark"])
        if seen < 0 or dark < 0 or dark > seen:
            raise ValueError(f"invalid counts on {day}: seen={seen}, dark={dark}")
        previous_seen, previous_dark = table.get(day, (0, 0))
        table[day] = (previous_seen + seen, previous_dark + dark)
    return table


def _rows_between(
    table: Mapping[date, tuple[int, int]],
    start: date,
    end: date,
    min_seen: int,
) -> list[Row]:
    """Well-covered rows in an inclusive date range, sorted by day."""
    return [
        (day, seen, dark)
        for day, (seen, dark) in sorted(table.items())
        if start <= day <= end and seen >= min_seen
    ]


def _summary(rows: Sequence[Row]) -> dict[str, Any]:
    """Pooled dark share plus the counts a reader needs to audit it."""
    if not rows:
        return {"dark_share": None, "seen": 0, "dark": 0, "days": 0}
    seen = sum(row[1] for row in rows)
    dark = sum(row[2] for row in rows)
    if seen == 0:
        return {"dark_share": None, "seen": 0, "dark": dark, "days": len(rows)}
    return {"dark_share": dark / seen, "seen": seen, "dark": dark, "days": len(rows)}


def _change(before: Mapping[str, Any], after: Mapping[str, Any]) -> float | None:
    if before["dark_share"] is None or after["dark_share"] is None:
        return None
    return float(after["dark_share"]) - float(before["dark_share"])


def _did(
    treated_before: Mapping[str, Any],
    treated_after: Mapping[str, Any],
    control_before: Mapping[str, Any],
    control_after: Mapping[str, Any],
) -> float | None:
    treated_change = _change(treated_before, treated_after)
    control_change = _change(control_before, control_after)
    if treated_change is None or control_change is None:
        return None
    return treated_change - control_change


def _bootstrap_shares(
    rng: np.random.Generator,
    rows: Sequence[Row],
    n_boot: int,
) -> np.ndarray:
    """Resample whole days with replacement and return one pooled share per replicate."""
    seen = np.array([row[1] for row in rows], dtype=float)
    dark = np.array([row[2] for row in rows], dtype=float)
    idx = rng.integers(0, seen.size, size=(n_boot, seen.size))
    totals = seen[idx].sum(axis=1)
    dark_totals = dark[idx].sum(axis=1)
    return np.divide(
        dark_totals,
        totals,
        out=np.full(n_boot, np.nan),
        where=totals != 0.0,
    )


def _bootstrap_did(
    rng: np.random.Generator,
    treated_before: Sequence[Row],
    treated_after: Sequence[Row],
    control_before: Sequence[Row],
    control_after: Sequence[Row],
    point: float,
    n_boot: int,
    alpha: float,
) -> tuple[float, float]:
    """Percentile interval for the difference-in-differences from independent day blocks.

    Days are the resampling unit and each of the four windows is resampled separately,
    because the two areas and the two periods are independent samples. The interval is
    widened by the point estimate if necessary so a reported interval always contains the
    number it qualifies; with percentile intervals that is not otherwise guaranteed at
    very small day counts.
    """
    tb = _bootstrap_shares(rng, treated_before, n_boot)
    ta = _bootstrap_shares(rng, treated_after, n_boot)
    cb = _bootstrap_shares(rng, control_before, n_boot)
    ca = _bootstrap_shares(rng, control_after, n_boot)
    replicates = (ta - tb) - (ca - cb)
    replicates = replicates[np.isfinite(replicates)]
    if replicates.size == 0:
        return (point, point)
    lo = float(np.percentile(replicates, 100.0 * alpha / 2.0))
    hi = float(np.percentile(replicates, 100.0 * (1.0 - alpha / 2.0)))
    return (min(lo, point), max(hi, point))


def _verdict(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return VERDICT_INSUFFICIENT
    if interval[0] <= 0.0 <= interval[1]:
        return VERDICT_NONE
    return VERDICT_MOVES


def _validate_params(window_days: int, min_seen: int, n_boot: int, alpha: float) -> None:
    if window_days < 1:
        raise ValueError("window_days must be at least 1")
    if min_seen < 0:
        raise ValueError("min_seen must be non-negative")
    if n_boot < 1:
        raise ValueError("n_boot must be at least 1")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")


def _event_bounds(event: Mapping[str, Any], window_days: int) -> tuple[date, date, date, date]:
    start = _as_date(event["date_start"])
    end = _as_date(event["date_end"])
    if end < start:
        raise ValueError(f"event ends before it starts: {start} .. {end}")
    before = (start - timedelta(days=window_days), start - timedelta(days=1))
    after = (end + timedelta(days=1), end + timedelta(days=window_days))
    return before[0], before[1], after[0], after[1]


def event_study(
    treated: Sequence[Mapping[str, Any]],
    control: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_seen: int = DEFAULT_MIN_SEEN,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> list[dict[str, Any]]:
    """Compare dark share around each event in the treated area against the control.

    Each event uses ``window_days`` full days immediately before ``date_start`` and the
    same number immediately after ``date_end``. A side with no day clearing ``min_seen``
    makes the event ``"insufficient coverage"`` rather than silently comparing a partial
    window. Returned dicts carry both areas' pre/post summaries, the point DiD, its
    interval and the non-causal verdict.
    """
    _validate_params(window_days, min_seen, n_boot, alpha)
    treated_table = _daily_table(treated)
    control_table = _daily_table(control)
    rng = np.random.default_rng(seed)

    results: list[dict[str, Any]] = []
    for event in events:
        pre_start, pre_end, post_start, post_end = _event_bounds(event, window_days)
        windows = {
            "treated_pre": _rows_between(treated_table, pre_start, pre_end, min_seen),
            "treated_post": _rows_between(treated_table, post_start, post_end, min_seen),
            "control_pre": _rows_between(control_table, pre_start, pre_end, min_seen),
            "control_post": _rows_between(control_table, post_start, post_end, min_seen),
        }
        summaries = {name: _summary(rows) for name, rows in windows.items()}
        insufficient = any(summary["days"] == 0 for summary in summaries.values())

        did: float | None = None
        interval: tuple[float, float] | None = None
        if not insufficient:
            did = _did(
                summaries["treated_pre"],
                summaries["treated_post"],
                summaries["control_pre"],
                summaries["control_post"],
            )
            if did is not None:
                interval = _bootstrap_did(
                    rng,
                    windows["treated_pre"],
                    windows["treated_post"],
                    windows["control_pre"],
                    windows["control_post"],
                    did,
                    n_boot,
                    alpha,
                )

        results.append(
            {
                "date_start": _as_date(event["date_start"]).isoformat(),
                "date_end": _as_date(event["date_end"]).isoformat(),
                "title": str(event.get("title", "")),
                "type": event.get("type"),
                "source_url": event.get("source_url"),
                "confidence": event.get("confidence"),
                "window_days": window_days,
                "min_seen": min_seen,
                "treated": {
                    "pre": summaries["treated_pre"],
                    "post": summaries["treated_post"],
                    "change": _change(summaries["treated_pre"], summaries["treated_post"]),
                },
                "control": {
                    "pre": summaries["control_pre"],
                    "post": summaries["control_post"],
                    "change": _change(summaries["control_pre"], summaries["control_post"]),
                },
                "did": did,
                "did_ci": list(interval) if interval is not None else None,
                "verdict": _verdict(interval),
            }
        )
    return results


def _as_range(value: Sequence[Any]) -> tuple[date, date]:
    if len(value) != 2:
        raise ValueError("a date range needs exactly (start, end)")
    start, end = _as_date(value[0]), _as_date(value[1])
    if end < start:
        raise ValueError(f"range ends before it starts: {start} .. {end}")
    return start, end


def regime_comparison(
    treated: Sequence[Mapping[str, Any]],
    control: Sequence[Mapping[str, Any]],
    baseline: Sequence[Any],
    current: Sequence[Any],
    *,
    min_seen: int = DEFAULT_MIN_SEEN,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    """Compare one period against another in both areas, returning their DiD and interval.

    ``baseline`` and ``current`` are ``(start, end)`` pairs of dates or ISO strings. This
    is the same estimator as :func:`event_study` without an event, used to describe a
    longer regime shift (for example 2025 against a 2026 window).
    """
    _validate_params(1, min_seen, n_boot, alpha)
    baseline_start, baseline_end = _as_range(baseline)
    current_start, current_end = _as_range(current)
    treated_table = _daily_table(treated)
    control_table = _daily_table(control)
    rng = np.random.default_rng(seed)

    windows = {
        "treated_baseline": _rows_between(treated_table, baseline_start, baseline_end, min_seen),
        "treated_current": _rows_between(treated_table, current_start, current_end, min_seen),
        "control_baseline": _rows_between(control_table, baseline_start, baseline_end, min_seen),
        "control_current": _rows_between(control_table, current_start, current_end, min_seen),
    }
    summaries = {name: _summary(rows) for name, rows in windows.items()}
    insufficient = any(summary["days"] == 0 for summary in summaries.values())

    did: float | None = None
    interval: tuple[float, float] | None = None
    if not insufficient:
        did = _did(
            summaries["treated_baseline"],
            summaries["treated_current"],
            summaries["control_baseline"],
            summaries["control_current"],
        )
        if did is not None:
            interval = _bootstrap_did(
                rng,
                windows["treated_baseline"],
                windows["treated_current"],
                windows["control_baseline"],
                windows["control_current"],
                did,
                n_boot,
                alpha,
            )

    return {
        "baseline": {"start": baseline_start.isoformat(), "end": baseline_end.isoformat()},
        "current": {"start": current_start.isoformat(), "end": current_end.isoformat()},
        "min_seen": min_seen,
        "treated": {
            "baseline": summaries["treated_baseline"],
            "current": summaries["treated_current"],
            "change": _change(summaries["treated_baseline"], summaries["treated_current"]),
        },
        "control": {
            "baseline": summaries["control_baseline"],
            "current": summaries["control_current"],
            "change": _change(summaries["control_baseline"], summaries["control_current"]),
        },
        "did": did,
        "did_ci": list(interval) if interval is not None else None,
        "verdict": _verdict(interval),
    }


def monthly_coverage(
    series: Sequence[Mapping[str, Any]],
    *,
    min_seen: int = DEFAULT_MIN_SEEN,
) -> list[dict[str, Any]]:
    """Per calendar month: radar days used and mean detections on those days.

    "Used" means a day cleared ``min_seen``; ``days_present`` counts every day in the
    file so a reader can see how much of the month was dropped.
    """
    if min_seen < 0:
        raise ValueError("min_seen must be non-negative")
    table = _daily_table(series)
    by_month: dict[str, list[tuple[int, int]]] = {}
    for day, (seen, dark) in sorted(table.items()):
        by_month.setdefault(f"{day.year:04d}-{day.month:02d}", []).append((seen, dark))

    out: list[dict[str, Any]] = []
    for month, rows in sorted(by_month.items()):
        used = [row for row in rows if row[0] >= min_seen]
        seen_total = sum(row[0] for row in used)
        dark_total = sum(row[1] for row in used)
        out.append(
            {
                "month": month,
                "days_present": len(rows),
                "days_used": len(used),
                "mean_seen_per_day": seen_total / len(used) if used else None,
                "mean_dark_per_day": dark_total / len(used) if used else None,
                "dark_share": dark_total / seen_total if seen_total else None,
            }
        )
    return out
