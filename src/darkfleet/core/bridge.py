"""Brownian-bridge likelihood helpers for an AIS gap.

A gap only tells us the vessel was at ``a`` at ``t0`` and at ``b`` at ``t1``. The
geodesic path between the two fixes is the most plausible route, but a real hull
drifts around it. The Brownian bridge movement model (Horne et al. 2007, Ecology
88(9)) gives that intuition a density: the expected position is the geodesic
interpolation of the two fixes, and the uncertainty is largest mid-gap and pinches
to the floor at the endpoints. Scoring a radar cell with this density lets us rank
candidate dark sightings without first reconstructing a track.

Everything here is pure (no I/O) so it can be unit-tested in isolation and reused by
the validation code. Distances are WGS84 geodesics rather than planar
approximations: at 100 km a 1% distance error moves the likelihood enough to
reorder candidates.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import numpy as np

from darkfleet.core.geo import GEOD, Fix, distance_km, distance_km_array
from darkfleet.core.prism import _reach_km

S2_FLOOR_KM2 = 1.0  # variance floor so the density is finite at t0 and t1


def _gap_hours(a: Fix, b: Fix) -> float:
    return (b.t - a.t).total_seconds() / 3600.0


def _tau(a: Fix, b: Fix, t: datetime) -> float:
    """Fraction of the gap elapsed at t, clamped to [0, 1].

    Clamping matters because the day sampler and external callers may probe just
    outside the gap; a tau outside [0, 1] would put the mean off the A-B geodesic and
    make the variance negative.
    """
    hours = _gap_hours(a, b)
    if hours <= 0.0:
        return 0.0
    return min(1.0, max(0.0, (t - a.t).total_seconds() / 3600.0 / hours))


def bridge_mean(a: Fix, b: Fix, t: datetime) -> tuple[float, float]:
    """(lat, lon) of the geodesic interpolation at time t.

    The mean follows the great circle from A to B at constant speed. Using the
    geodesic (``GEOD.inv`` for the initial azimuth and length, then ``GEOD.fwd``)
    rather than linear lat/lon interpolation keeps the midpoint equidistant from both
    fixes even at high latitudes, where a naive interpolation drifts by kilometres.
    """
    tau = _tau(a, b, t)
    az12, _, dist_m = GEOD.inv(a.lon, a.lat, b.lon, b.lat)
    lon, lat, _ = GEOD.fwd(a.lon, a.lat, az12, dist_m * tau)
    return float(lat), float(lon)


def bridge_s2(a: Fix, b: Fix, sigma: float, t: datetime) -> float:
    """Per-axis variance (km^2) of the bridge at time t, floored at S2_FLOOR_KM2.

    A Brownian bridge's variance is proportional to ``tau * (1 - tau) * T``: zero at
    the known endpoints and maximal mid-gap. The floor is a pragmatic deviation from
    the pure model, since the unfloored density is a delta function at t0/t1 and
    would score every endpoint cell as infinite.
    """
    hours = _gap_hours(a, b)
    tau = _tau(a, b, t)
    return max(sigma * sigma * tau * (1.0 - tau) * hours, S2_FLOOR_KM2)


def bridge_logpdf(a: Fix, b: Fix, sigma: float, lat: float, lon: float, t: datetime) -> float:
    """Log density (per km^2) at (lat, lon) at time t.

    The model is an isotropic 2-D normal in the local tangent plane, so the only
    position input is the geodesic distance from the mean. Working in log space keeps
    far cells from underflowing to zero and lets ``day_scores`` compare them cheaply.
    """
    mlat, mlon = bridge_mean(a, b, t)
    s2 = bridge_s2(a, b, sigma, t)
    d = distance_km(mlat, mlon, lat, lon)
    return -math.log(2.0 * math.pi * s2) - (d * d) / (2.0 * s2)


def fit_sigma(d_km: np.ndarray, w_hours: np.ndarray) -> float:
    """Maximum-likelihood sigma from known positions and their bridge weights.

    ``d_km[i]`` is the distance of observation i from the bridge mean and
    ``w_hours[i]`` is its ``tau * (1 - tau) * T`` scale. For the isotropic model each
    squared distance divided by ``sigma^2 * w`` is chi-square with two degrees of
    freedom, whose MLE gives ``sigma^2 = sum(d^2 / w) / (2 n)``. Zero or negative
    weights carry no information about sigma and are dropped; a caller that supplies
    only those has no data and should hear about it rather than get a silent nan.
    """
    d = np.asarray(d_km, dtype=float).ravel()
    w = np.asarray(w_hours, dtype=float).ravel()
    keep = w > 0.0
    d = d[keep]
    w = w[keep]
    n = d.size
    if n == 0:
        raise ValueError("fit_sigma needs at least one observation with w > 0")
    return float(math.sqrt(float(np.sum(d * d / w)) / (2.0 * n)))


def day_scores(
    a: Fix,
    b: Fix,
    v_kn: float,
    sigma: float,
    lat: np.ndarray,
    lon: np.ndarray,
    day_ord: np.ndarray,
    step_minutes: int = 30,
) -> np.ndarray:
    """Best bridge log-density per radar cell on its assigned UTC day.

    A dark sighting is a *day*, not an instant, so a cell is scored by the most
    favourable instant that day at which the vessel could actually have been there.
    The speed cap makes that "could" exact: the cell must lie in the space-time lens
    for that instant, otherwise even a perfect positional match is physically
    impossible and must not earn a score (hence ``-inf``).

    The expensive work is vectorised per cell/day rather than per cell/time: the
    distances from A and B never change, and at each sample time the mean is a single
    point whose distance to every cell is one ``distance_km_array`` call.
    """
    lat = np.asarray(lat, dtype=float).ravel()
    lon = np.asarray(lon, dtype=float).ravel()
    day_ord = np.asarray(day_ord).ravel()
    n = lat.size
    out = np.full(n, -np.inf, dtype=float)
    if n == 0:
        return out

    d_a = distance_km_array(a.lat, a.lon, lat, lon)
    d_b = distance_km_array(b.lat, b.lon, lat, lon)

    for ord_value in np.unique(day_ord):
        idx = np.flatnonzero(day_ord == ord_value)
        day = date.fromordinal(int(ord_value))
        midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
        start = max(a.t, midnight)
        end = min(b.t, midnight + timedelta(days=1))
        if end < start:
            continue  # the day does not overlap the gap at all

        times: list[datetime] = []
        t = start
        while t < end:
            times.append(t)
            t += timedelta(minutes=step_minutes)
        times.append(end)

        cells_a = d_a[idx]
        cells_b = d_b[idx]
        cells_lat = lat[idx]
        cells_lon = lon[idx]
        best = np.full(idx.size, -np.inf, dtype=float)

        for when in times:
            reach_from_a = _reach_km(v_kn, (when - a.t).total_seconds())
            reach_to_b = _reach_km(v_kn, (b.t - when).total_seconds())
            inside = (cells_a <= reach_from_a + 1e-6) & (cells_b <= reach_to_b + 1e-6)
            if not inside.any():
                continue
            mlat, mlon = bridge_mean(a, b, when)
            s2 = bridge_s2(a, b, sigma, when)
            d_mean = distance_km_array(mlat, mlon, cells_lat, cells_lon)
            logpdf = -math.log(2.0 * math.pi * s2) - (d_mean * d_mean) / (2.0 * s2)
            np.maximum(best, np.where(inside, logpdf, -np.inf), out=best)

        out[idx] = best
    return out


def paired_auc(
    truth: np.ndarray,
    displaced: np.ndarray,
    rng: np.random.Generator,
    n_boot: int = 2000,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """AUC of true cells over displaced cells, with a percentile bootstrap interval.

    ``truth`` is (n,) and ``displaced`` is (n, m): for every true score we ask how
    often it beats that case's m sham positions, so the comparison stays paired and
    the bootstrap resamples the n cases (the independent units) rather than the
    individual pairs. Ties count 0.5 because a model that cannot separate two cells
    is at chance, not wrong in either direction.
    """
    truth_arr = np.asarray(truth, dtype=float).ravel()
    displaced_arr = np.asarray(displaced, dtype=float)
    if displaced_arr.ndim == 1:
        displaced_arr = displaced_arr.reshape(-1, 1)
    n, m = displaced_arr.shape
    if n == 0 or m == 0:
        return (math.nan, math.nan, math.nan)

    wins = truth_arr[:, None] > displaced_arr
    ties = truth_arr[:, None] == displaced_arr
    per_case = (wins.sum(axis=1) + 0.5 * ties.sum(axis=1)) / m
    auc = float(per_case.mean())

    idx = rng.integers(0, n, size=(n_boot, n))
    boot = per_case[idx].mean(axis=1)
    lo = float(np.percentile(boot, 100.0 * alpha / 2.0))
    hi = float(np.percentile(boot, 100.0 * (1.0 - alpha / 2.0)))
    return (auc, lo, hi)
