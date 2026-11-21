"""Deterministic tests for the Brownian-bridge helpers.

Every random draw uses an explicit seed so a failure is reproducible and the suite
can run in CI without flakes.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from darkfleet.core.bridge import (
    S2_FLOOR_KM2,
    bridge_logpdf,
    bridge_mean,
    bridge_s2,
    day_scores,
    fit_sigma,
    paired_auc,
)
from darkfleet.core.geo import Fix, distance_km

T0 = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)
A = Fix(24.0, 56.0, T0)
B = Fix(24.0, 57.0, T0 + timedelta(hours=24))
SIGMA = 5.0
KM_PER_DEG = 110.574  # meridional km per degree, close enough for the local-plane tests


def test_bridge_mean_endpoints_and_midpoint():
    lat, lon = bridge_mean(A, B, A.t)
    assert lat == pytest.approx(A.lat, abs=1e-6)
    assert lon == pytest.approx(A.lon, abs=1e-6)

    lat, lon = bridge_mean(A, B, B.t)
    assert lat == pytest.approx(B.lat, abs=1e-6)
    assert lon == pytest.approx(B.lon, abs=1e-6)

    mid = A.t + (B.t - A.t) / 2
    mlat, mlon = bridge_mean(A, B, mid)
    d_am = distance_km(A.lat, A.lon, mlat, mlon)
    d_mb = distance_km(mlat, mlon, B.lat, B.lon)
    d_ab = distance_km(A.lat, A.lon, B.lat, B.lon)
    assert abs(d_am - d_mb) < 0.01
    # on the geodesic: the two legs add up to the direct distance
    assert abs(d_am + d_mb - d_ab) < 0.01


def test_bridge_mean_clamps_to_endpoints():
    before = bridge_mean(A, B, A.t - timedelta(hours=3))
    after = bridge_mean(A, B, B.t + timedelta(hours=3))
    assert before == pytest.approx((A.lat, A.lon), abs=1e-6)
    assert after == pytest.approx((B.lat, B.lon), abs=1e-6)


def test_bridge_s2_floor_at_ends_and_max_at_midpoint():
    mid = A.t + (B.t - A.t) / 2
    total_h = (B.t - A.t).total_seconds() / 3600.0

    assert bridge_s2(A, B, SIGMA, A.t) == S2_FLOOR_KM2
    assert bridge_s2(A, B, SIGMA, B.t) == S2_FLOOR_KM2
    assert bridge_s2(A, B, SIGMA, mid) == pytest.approx(SIGMA**2 * total_h / 4.0)
    # tau*(1-tau) peaks at the midpoint, so no interior time can beat it
    assert bridge_s2(A, B, SIGMA, mid) > bridge_s2(A, B, SIGMA, A.t + (B.t - A.t) / 4)


def test_bridge_logpdf_peaks_at_mean_and_decays():
    a = Fix(0.0, 0.0, T0)
    b = Fix(0.0, 0.4, T0 + timedelta(hours=2))
    sigma = 20.0
    mid = a.t + (b.t - a.t) / 2
    mlat, mlon = bridge_mean(a, b, mid)

    peak = bridge_logpdf(a, b, sigma, mlat, mlon, mid)
    far = bridge_logpdf(a, b, sigma, mlat + 50.0 / KM_PER_DEG, mlon, mid)
    assert peak > far

    previous = peak
    for km in (10.0, 20.0, 30.0):
        value = bridge_logpdf(a, b, sigma, mlat + km / KM_PER_DEG, mlon, mid)
        assert value < previous
        previous = value


def test_bridge_logpdf_integrates_to_one():
    # Equatorial, short gap and a broad bridge so the local tangent plane is a good
    # approximation and a 0.02-degree grid resolves the normal well.
    a = Fix(0.0, 0.0, T0)
    b = Fix(0.0, 0.4, T0 + timedelta(hours=2))
    sigma = 20.0
    mid = a.t + (b.t - a.t) / 2
    mlat, mlon = bridge_mean(a, b, mid)

    offsets = np.arange(-0.8, 0.8 + 1e-9, 0.02)
    total = 0.0
    for dlat in offsets:
        for dlon in offsets:
            total += math.exp(bridge_logpdf(a, b, sigma, mlat + dlat, mlon + dlon, mid))

    cell_km2 = (0.02 * 110.574) * (0.02 * 111.320)
    assert total * cell_km2 == pytest.approx(1.0, abs=0.05)


def test_fit_sigma_recovers_true_sigma():
    rng = np.random.default_rng(20261007)
    n = 20000
    sigma_true = 3.0
    w = rng.uniform(1.0, 20.0, n)
    x = rng.normal(0.0, 1.0, n) * sigma_true * np.sqrt(w)
    y = rng.normal(0.0, 1.0, n) * sigma_true * np.sqrt(w)
    d = np.hypot(x, y)

    assert fit_sigma(d, w) == pytest.approx(sigma_true, rel=0.05)


def test_fit_sigma_drops_nonpositive_weights():
    d = np.array([1.0, 2.0, 100.0])
    w = np.array([1.0, 4.0, 0.0])
    expected = math.sqrt((1.0**2 / 1.0 + 2.0**2 / 4.0) / (2 * 2))
    assert fit_sigma(d, w) == pytest.approx(expected)


def test_fit_sigma_empty_raises():
    with pytest.raises(ValueError):
        fit_sigma(np.array([1.0, 2.0]), np.array([0.0, -1.0]))


def test_day_scores_inside_far_and_outside_gap():
    mid = A.t + (B.t - A.t) / 2
    mlat, mlon = bridge_mean(A, B, mid)
    day = A.t.date().toordinal()

    on_midpoint = day_scores(A, B, 20.0, SIGMA, np.array([mlat]), np.array([mlon]), np.array([day]))
    assert on_midpoint.shape == (1,)
    assert np.isfinite(on_midpoint[0])

    # 2000 km from both fixes: outside reach for the whole gap, so never in the lens.
    far_lat = 24.0 + 2000.0 / KM_PER_DEG
    far = day_scores(A, B, 20.0, SIGMA, np.array([far_lat]), np.array([56.5]), np.array([day]))
    assert not np.isfinite(far[0])

    outside = day_scores(
        A, B, 20.0, SIGMA, np.array([mlat]), np.array([mlon]), np.array([date(2026, 10, 9).toordinal()])
    )
    assert not np.isfinite(outside[0])


def test_day_scores_geodesic_beats_offset():
    mid = A.t + (B.t - A.t) / 2
    mlat, mlon = bridge_mean(A, B, mid)
    off_lat = mlat + 30.0 / KM_PER_DEG
    day = A.t.date().toordinal()

    scores = day_scores(
        A,
        B,
        20.0,
        SIGMA,
        np.array([mlat, off_lat]),
        np.array([mlon, mlon]),
        np.array([day, day]),
    )
    assert np.isfinite(scores).all()
    assert scores[0] > scores[1]


def test_day_scores_empty_input():
    out = day_scores(A, B, 20.0, SIGMA, np.empty(0), np.empty(0), np.empty(0, dtype=np.int64))
    assert out.shape == (0,)


def test_paired_auc_perfect_separation():
    rng = np.random.default_rng(7)
    truth = np.array([5.0, 6.0, 7.0, 8.0])
    displaced = np.zeros((4, 3))

    auc, lo, _hi = paired_auc(truth, displaced, rng, n_boot=500)
    assert auc == 1.0
    assert lo > 0.9


def test_paired_auc_identical_is_half():
    rng = np.random.default_rng(8)
    truth = np.arange(5.0)
    displaced = np.repeat(truth[:, None], 4, axis=1)

    auc, lo, hi = paired_auc(truth, displaced, rng, n_boot=500)
    assert auc == pytest.approx(0.5)
    assert lo == pytest.approx(0.5)
    assert hi == pytest.approx(0.5)
