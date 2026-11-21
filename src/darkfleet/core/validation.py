"""Pure statistics and geodesy helpers for validating a dark-vessel detector.

Nothing here touches I/O: every stochastic routine takes an explicit
``numpy.random.Generator`` so a validation run is reproducible from a seed alone, and
the statistical helpers are written as plain numbers so they can be tested in isolation
from the matching pipeline that produces the counts.
"""

from __future__ import annotations

import math

import numpy as np

from darkfleet.core.geo import GEOD


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion k/n.

    Wilson rather than the normal approximation because hit rates are often near 0 or 1
    (e.g. 50/50), where the normal interval collapses to zero width and leaks outside
    [0, 1]. Bounds are clipped so callers never see an out-of-range probability.
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    lo = min(1.0, max(0.0, center - half))
    hi = min(1.0, max(0.0, center + half))
    return (lo, hi)


def allowed_shifts(lo: int, hi: int, day_min: int, day_max: int) -> np.ndarray:
    """Signed integer day shifts d with lo <= |d| <= hi and day_min <= d <= day_max.

    The two bounds answer different questions: ``lo``/``hi`` keep a gap from collapsing
    onto itself or from being matched absurdly far away, while ``day_min``/``day_max``
    clip to the observation window. Keeping them separate lets callers tighten the
    window without rewriting the distance policy.
    """
    hi_i = int(hi)
    start = max(int(day_min), -hi_i)
    stop = min(int(day_max), hi_i)
    if start > stop:
        return np.empty(0, dtype=np.int64)
    candidates = np.arange(start, stop + 1, dtype=np.int64)
    return candidates[np.abs(candidates) >= int(lo)]


def draw_shifts(rng: np.random.Generator, allowed: list[np.ndarray], k: int) -> np.ndarray:
    """Draw k shifts per gap, uniformly from each gap's own allowed set, with replacement.

    Sampling per gap (rather than from a pooled set) preserves the null hypothesis that
    a gap's match could have happened on any plausible day for *that* gap. Empty sets get
    the sentinel 0 so the result stays rectangular; the caller's valid mask drops them.
    """
    n = len(allowed)
    out = np.zeros((n, k), dtype=np.int64)
    for i, options in enumerate(allowed):
        arr = np.asarray(options, dtype=np.int64).ravel()
        if arr.size == 0 or k == 0:
            continue
        out[i] = rng.choice(arr, size=k, replace=True)
    return out


def paired_ratio_ci(
    real: np.ndarray,
    shuffled: np.ndarray,
    rng: np.random.Generator,
    n_boot: int = 2000,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Bootstrap interval for how much better the real gaps match than chance.

    ``real`` is (n,) and ``shuffled`` is (n, K); the point estimate is the real hit count
    over the mean shuffled hit count per replicate. Gaps are resampled (not replicates)
    because gaps, not shuffles, are the independent units, and the same index is applied
    to both arrays so the pairing is preserved. Replicates with a zero denominator are
    undefined and dropped instead of being coerced to inf.
    """
    real_arr = np.asarray(real, dtype=float).ravel()
    shuffled_arr = np.asarray(shuffled, dtype=float)
    if shuffled_arr.ndim == 1:
        shuffled_arr = shuffled_arr.reshape(-1, 1)
    n = real_arr.size
    if n == 0:
        return (math.nan, math.nan, math.nan)

    # mean over K of sum over gaps == sum over gaps of the per-gap mean, which is the
    # cheap form: only n numbers per replicate need resampling.
    per_gap_shuffled = shuffled_arr.mean(axis=1)
    point_mean = float(per_gap_shuffled.sum())
    if point_mean == 0.0:
        return (math.nan, math.nan, math.nan)
    ratio = float(real_arr.sum()) / point_mean

    idx = rng.integers(0, n, size=(n_boot, n))
    real_boot = real_arr[idx].sum(axis=1)
    shuffled_boot = per_gap_shuffled[idx].sum(axis=1)
    valid = shuffled_boot != 0.0
    ratios = real_boot[valid] / shuffled_boot[valid]
    if ratios.size == 0:
        return (ratio, math.nan, math.nan)

    lo = float(np.percentile(ratios, 100.0 * alpha / 2.0))
    hi = float(np.percentile(ratios, 100.0 * (1.0 - alpha / 2.0)))
    return (ratio, lo, hi)


def null_summary(real_count: int, shuffled_counts: np.ndarray) -> dict:
    """Summarise the shuffle null distribution against the real hit count.

    The add-one p-value keeps a perfect score from reporting p = 0, which would be a
    claim no finite number of shuffles can support. ``null_lo``/``null_hi`` and the ratio
    give a human-readable sense of scale next to the p-value.
    """
    counts = np.asarray(shuffled_counts, dtype=float).ravel()
    k = int(counts.size)
    real = int(real_count)
    if k == 0:
        return {
            "real": real,
            "k": 0,
            "shuffled_mean": math.nan,
            "shuffled_sd": 0.0,
            "null_lo": math.nan,
            "null_hi": math.nan,
            "ratio": math.nan,
            "p_value": 1.0,
        }
    mean = float(counts.mean())
    sd = float(counts.std(ddof=1)) if k >= 2 else 0.0
    null_lo = float(np.percentile(counts, 2.5))
    null_hi = float(np.percentile(counts, 97.5))
    ratio = float(real) / mean if mean != 0.0 else math.nan
    p_value = float(1 + int(np.count_nonzero(counts >= real))) / float(1 + k)
    return {
        "real": real,
        "k": k,
        "shuffled_mean": mean,
        "shuffled_sd": sd,
        "null_lo": null_lo,
        "null_hi": null_hi,
        "ratio": ratio,
        "p_value": p_value,
    }


def interpret_ratio(ratio: float, lo: float, hi: float) -> str:
    """Turn a ratio and its interval into the sentence a report can print.

    NaN is checked first because it means there was nothing to compare against, which is
    a different failure from "the interval straddles 1" and should not be reported as a
    negative result.
    """
    if math.isnan(ratio) or math.isnan(lo) or math.isnan(hi):
        return "undefined (no chance-level matches to compare against)"
    if lo > 1.0:
        return "above chance (interval excludes 1)"
    if hi < 1.0:
        return "below chance (interval excludes 1)"
    return "not distinguishable from chance (interval includes 1)"


def offset_point(lat: float, lon: float, dist_km: float, bearing_deg: float) -> tuple[float, float]:
    """Move a point along a WGS84 geodesic, returning (lat, lon).

    pyproj's ``fwd`` is (lon, lat, azimuth, metres), which is easy to call backwards;
    this wrapper fixes the axis order once so every caller cannot get it wrong.
    """
    lon2, lat2, _ = GEOD.fwd(lon, lat, bearing_deg, dist_km * 1000.0)
    return (float(lat2), float(lon2))


def random_offsets(
    rng: np.random.Generator,
    n: int,
    lo_km: float = 50.0,
    hi_km: float = 150.0,
) -> tuple[np.ndarray, np.ndarray]:
    """n random (distance_km, bearing_deg) pairs for displaced-position nulls.

    Uniform in distance is deliberate: it scatters false positives over the whole
    annulus instead of clustering them just outside the real footprint, which is the
    mistake we are trying to rule out.
    """
    dist_km = rng.uniform(lo_km, hi_km, size=n)
    bearing_deg = rng.uniform(0.0, 360.0, size=n)
    return (dist_km, bearing_deg)
