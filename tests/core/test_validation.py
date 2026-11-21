"""Tests for the pure validation helpers.

Every test is seeded and small so a failure points at one formula rather than at the
detector; these helpers are the measuring stick, so they must be right before any
accuracy number is trusted.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from darkfleet.core.geo import distance_km
from darkfleet.core.validation import (
    allowed_shifts,
    draw_shifts,
    interpret_ratio,
    null_summary,
    offset_point,
    paired_ratio_ci,
    random_offsets,
    wilson_interval,
)


def test_wilson_edge_cases() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(50, 50)
    assert hi == 1.0
    assert 0.9 < lo < 1.0
    assert wilson_interval(0, 50)[0] == 0.0


def test_wilson_is_symmetric() -> None:
    lo10, hi10 = wilson_interval(10, 40)
    lo30, hi30 = wilson_interval(30, 40)
    assert lo10 == pytest.approx(1.0 - hi30, abs=1e-12)
    assert hi10 == pytest.approx(1.0 - lo30, abs=1e-12)


def test_allowed_shifts_full_range() -> None:
    shifts = allowed_shifts(7, 21, -100, 100)
    assert shifts.size == 30
    assert np.all(np.abs(shifts) >= 7)


def test_allowed_shifts_clipped_to_window() -> None:
    shifts = allowed_shifts(7, 21, -10, 8)
    assert list(shifts) == [-10, -9, -8, -7, 7, 8]


def test_allowed_shifts_empty_when_window_excludes_them() -> None:
    assert allowed_shifts(7, 21, 0, 3).size == 0


def test_draw_shifts_shape_and_membership() -> None:
    rng = np.random.default_rng(0)
    allowed = [np.array([7, 8]), np.array([-7, -1, 3]), np.empty(0, dtype=int), np.array([21])]
    draws = draw_shifts(rng, allowed, k=5)
    assert draws.shape == (4, 5)
    for row, options in zip(draws, allowed, strict=True):
        if np.asarray(options).size == 0:
            assert np.all(row == 0)
            continue
        assert set(row.tolist()) <= set(np.asarray(options).tolist())


def test_draw_shifts_empty_set_gives_sentinel_zeros() -> None:
    rng = np.random.default_rng(0)
    draws = draw_shifts(rng, [np.empty(0, dtype=int)], k=4)
    assert np.array_equal(draws, np.zeros((1, 4), dtype=np.int64))


def test_draw_shifts_reproducible_for_same_seed() -> None:
    allowed = [np.array([7, 8, 9]), np.array([-3, -2])]
    first = draw_shifts(np.random.default_rng(0), allowed, k=6)
    second = draw_shifts(np.random.default_rng(0), allowed, k=6)
    assert np.array_equal(first, second)


def test_paired_ratio_ci_ratio_one_contains_one() -> None:
    rng = np.random.default_rng(0)
    real = np.ones(100, dtype=int)
    shuffled = np.ones((100, 20), dtype=int)
    ratio, lo, hi = paired_ratio_ci(real, shuffled, rng)
    assert ratio == pytest.approx(1.0)
    assert lo <= 1.0 <= hi


def test_paired_ratio_ci_ten_percent_null_gives_ratio_near_ten() -> None:
    rng = np.random.default_rng(0)
    real = np.ones(100, dtype=int)
    shuffled = (rng.random((100, 200)) < 0.1).astype(int)
    ratio, lo, hi = paired_ratio_ci(real, shuffled, rng)
    assert ratio == pytest.approx(10.0, rel=0.2)
    assert lo > 1.0
    assert lo <= ratio <= hi


def test_paired_ratio_ci_zero_null_is_nan() -> None:
    rng = np.random.default_rng(0)
    ratio, lo, hi = paired_ratio_ci(np.ones(10, dtype=int), np.zeros((10, 5), dtype=int), rng)
    assert math.isnan(ratio) and math.isnan(lo) and math.isnan(hi)


def test_null_summary_ratio_and_p_value() -> None:
    summary = null_summary(100, np.array([10] * 50))
    assert summary["real"] == 100
    assert summary["k"] == 50
    assert summary["shuffled_mean"] == pytest.approx(10.0)
    assert summary["shuffled_sd"] == 0.0
    assert summary["null_lo"] == 10.0
    assert summary["null_hi"] == 10.0
    assert summary["ratio"] == pytest.approx(10.0)
    assert summary["p_value"] == pytest.approx(1.0 / 51.0)
    assert all(
        isinstance(summary[key], float) for key in ("shuffled_mean", "shuffled_sd", "ratio", "p_value")
    )


def test_null_summary_p_value_saturates_at_one() -> None:
    summary = null_summary(5, np.array([10] * 50))
    assert summary["p_value"] == 1.0


def test_interpret_ratio_branches() -> None:
    assert interpret_ratio(1.5, 1.1, 2.0) == "above chance (interval excludes 1)"
    assert interpret_ratio(0.5, 0.2, 0.9) == "below chance (interval excludes 1)"
    assert interpret_ratio(1.1, 0.9, 1.3) == "not distinguishable from chance (interval includes 1)"
    assert interpret_ratio(math.nan, 0.9, 1.3) == "undefined (no chance-level matches to compare against)"


def test_offset_point_distance_and_direction() -> None:
    lat, lon = 0.0, 0.0
    for dist_km, bearing in [(50.0, 0.0), (100.0, 90.0), (150.0, 200.0)]:
        lat2, lon2 = offset_point(lat, lon, dist_km, bearing)
        assert distance_km(lat, lon, lat2, lon2) == pytest.approx(dist_km, abs=0.01)
    north_lat, _ = offset_point(lat, lon, 100.0, 0.0)
    assert north_lat > lat


def test_random_offsets_ranges() -> None:
    rng = np.random.default_rng(0)
    dist_km, bearing = random_offsets(rng, n=1000)
    assert dist_km.shape == (1000,)
    assert bearing.shape == (1000,)
    assert np.all((dist_km >= 50.0) & (dist_km <= 150.0))
    assert np.all((bearing >= 0.0) & (bearing < 360.0))
