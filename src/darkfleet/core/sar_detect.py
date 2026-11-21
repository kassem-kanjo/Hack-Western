"""Ship detection on Sentinel-1 radar: two-parameter CFAR (pure numpy, no I/O).

Input is VV backscatter in linear power (sigma0 / gamma0, 10 m pixels) and a boolean land mask.
Open water is dark and speckly; a ship is a few pixels much brighter than the water around it.
CFAR ("constant false alarm rate") makes the cut-off adapt to the local sea state:

1. For each pixel, estimate the clutter around it from a ring of pixels (outside a guard box that
   keeps the ship itself out of its own background): mean ``mu`` and standard deviation ``sigma``.
   Land, no-data and very bright pixels are left out of the ring.
2. Flag the pixel when ``(x - mu) / sigma > k``  (k = ``DetectorParams.k``).
3. Join flagged pixels that touch (within ``merge_px``) into blobs, and keep blobs whose size is
   plausible for a ship. Report centroid (pixel coordinates), area in pixels and peak power.

Parameters (10 m pixels), all in :class:`DetectorParams`:

* ``guard`` 25 px  (side of the box excluded from the background, 250 m: larger than any ship we keep)
* ``ring`` 61 px   (side of the outer background box, 610 m)
* ``k`` 6.0        (threshold in sigmas over the local mean. Radar speckle in 4-look GRD data has a
                    standard deviation about half its mean, so k = 6 means ~4x the local mean)
* ``min_area`` 3 px, ``max_area`` 1500 px (30 m^2 .. 0.15 km^2), ``max_extent`` 80 px (800 m)
* ``merge_px`` 2   (bright ship parts separated by <= 2 dark pixels are one ship)
* ``min_peak_ratio`` 25 (the brightest pixel must be >= 25x the local mean, i.e. +14 dB: below that,
                    wind streaks, internal waves and shore fringes outnumber ships)
* ``max_background_db`` -8 dB (a background brighter than this is surf, rain, land edge or an
                    urban/port clutter: skipped rather than guessed)

The land mask is the caller's job (rasterise a land polygon on the pixel grid and grow it by a few
hundred metres with :func:`dilate`): the 10 m Natural Earth coastline is not accurate to a pixel, and
ships beside a quay are exactly where radar is least reliable. Pixels this module cannot judge are
never reported.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DetectorParams:
    guard: int = 25
    ring: int = 61
    k: float = 6.0
    min_area: int = 3
    max_area: int = 1500
    max_extent: int = 80
    merge_px: int = 2
    min_peak_ratio: float = 25.0
    max_background_db: float = -8.0
    min_ring_fraction: float = 0.4
    tile_rows: int = 1024


@dataclass(frozen=True)
class Detection:
    row: float  # centroid, pixel coordinates in the input array
    col: float
    area_px: int
    peak: float  # brightest pixel, linear power
    peak_db: float
    background: float  # local clutter mean at the brightest pixel, linear power
    contrast_db: float  # peak over local clutter, dB


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow a boolean mask by ``radius`` pixels (square structuring element, log-time doubling)."""
    if radius <= 0:
        return mask.copy()
    size = 2 * radius + 1
    out = mask.copy()
    for axis in (0, 1):
        n = out.shape[axis]
        pad = [(0, 0), (0, 0)]
        pad[axis] = (0, radius)  # room at the far end so the window can be centred
        out = np.pad(out, pad)
        covered = 1
        while covered < size:  # now out[i] = any(in[i-size+1 .. i])
            step = min(covered, size - covered)
            shifted = np.zeros_like(out)
            if axis == 0:
                shifted[step:] = out[:-step]
            else:
                shifted[:, step:] = out[:, :-step]
            out = out | shifted
            covered += step
        out = out[radius : radius + n] if axis == 0 else out[:, radius : radius + n]  # centre: [i-r, i+r]
    return out


def bright_land_mask(
    power: np.ndarray,
    *,
    window: int = 31,
    threshold_db: float = -14.0,
    min_fraction: float = 0.5,
    grow_px: int = 12,
) -> np.ndarray:
    """Land (and surf, rigs, quays) found from the radar itself: wide patches of bright texture.

    Open sea in VV is around -22 dB; hills, towns and breaking surf are brighter than -14 dB over
    large areas. A pixel is land when more than ``min_fraction`` of the ``window`` x ``window``
    pixels around it exceed ``threshold_db``. A ship covers well under half of such a window, so it
    does not mask itself. The result is grown by ``grow_px`` to swallow the shoreline fringe. This
    backs up the vector land mask, whose 10 m Natural Earth coastline is only accurate to a few
    hundred metres and misses steep, radar-bright shores.
    """
    bright = np.isfinite(power) & (power > 10.0 ** (threshold_db / 10.0))
    frac = np.zeros(power.shape, dtype=bool)
    step = 1024
    half = window // 2
    for r0 in range(0, power.shape[0], step):
        a, b = max(0, r0 - half), min(power.shape[0], r0 + step + half)
        s = _box_sum(bright[a:b], window) / float(window * window)
        frac[r0 : min(r0 + step, power.shape[0])] = (
            s[r0 - a : r0 - a + min(step, power.shape[0] - r0)] > min_fraction
        )
    return dilate(frac, grow_px)


def _box_sum(a: np.ndarray, size: int) -> np.ndarray:
    """Sum over a ``size`` x ``size`` window centred on each pixel (zero padded), via integral image."""
    half = size // 2
    padded = np.pad(a.astype(np.float64), half)
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1))
    integral[1:, 1:] = padded.cumsum(0).cumsum(1)
    h, w = a.shape
    return (
        integral[size : size + h, size : size + w]
        - integral[0:h, size : size + w]
        - integral[size : size + h, 0:w]
        + integral[0:h, 0:w]
    )


def _cfar_tile(
    x: np.ndarray, usable: np.ndarray, p: DetectorParams, clip: float
) -> tuple[np.ndarray, np.ndarray]:
    """Boolean hit map and local clutter mean for one tile (halo already included)."""
    w = np.where(usable, np.minimum(x, clip), 0.0)
    n = usable.astype(np.float64)
    s1 = _box_sum(w, p.ring) - _box_sum(w, p.guard)
    s2 = _box_sum(w * w, p.ring) - _box_sum(w * w, p.guard)
    cnt = _box_sum(n, p.ring) - _box_sum(n, p.guard)
    full = float(p.ring * p.ring - p.guard * p.guard)
    ok = cnt >= p.min_ring_fraction * full
    safe = np.where(cnt > 0, cnt, 1.0)
    mu = s1 / safe
    var = np.maximum(s2 / safe - mu * mu, 0.0)
    sigma = np.sqrt(var)
    floor = 1e-12
    hit = (
        ok
        & usable
        & (mu > floor)
        & (mu < 10.0 ** (p.max_background_db / 10.0))
        & (x > mu + p.k * np.maximum(sigma, floor))
        & (x > p.min_peak_ratio * mu)
    )
    return hit, mu


def detect_ships(
    power: np.ndarray, land: np.ndarray | None = None, params: DetectorParams = DetectorParams()
) -> list[Detection]:
    """Ships in a linear-power radar image. ``land`` is True where the pixel must be ignored."""
    p = params
    power = np.asarray(power)
    if power.ndim != 2:
        raise ValueError("power must be a 2-D array")
    usable_all = np.isfinite(power) & (power > 0)
    if land is not None:
        if land.shape != power.shape:
            raise ValueError("land mask must match the image shape")
        usable_all &= ~land
    if not usable_all.any():
        return []
    # Cap pixels at 30x the sea median when estimating clutter, so a ship cannot lift its own background.
    median = float(np.median(power[usable_all][:: max(1, int(usable_all.sum()) // 200_000)]))
    clip = 30.0 * median

    halo = p.ring // 2
    rows_list: list[np.ndarray] = []
    cols_list: list[np.ndarray] = []
    mus: list[np.ndarray] = []
    h = power.shape[0]
    for r0 in range(0, h, p.tile_rows):
        r1 = min(h, r0 + p.tile_rows)
        a, b = max(0, r0 - halo), min(h, r1 + halo)
        x = power[a:b].astype(np.float64)
        hit, mu = _cfar_tile(x, usable_all[a:b], p, clip)
        hit = hit[r0 - a : r1 - a]
        rr, cc = np.nonzero(hit)
        rows_list.append(rr + r0)
        cols_list.append(cc)
        mus.append(mu[r0 - a : r1 - a][rr, cc])
    rows = np.concatenate(rows_list)
    cols = np.concatenate(cols_list)
    bg = np.concatenate(mus)
    if rows.size == 0:
        return []
    return _blobs(power, rows, cols, bg, p)


def _blobs(
    power: np.ndarray, rows: np.ndarray, cols: np.ndarray, bg: np.ndarray, p: DetectorParams
) -> list[Detection]:
    """Group flagged pixels into blobs (union of pixels within ``merge_px``) and filter by size."""
    width = power.shape[1] + 2 * p.merge_px + 1
    key = rows.astype(np.int64) * width + cols
    order = np.argsort(key)
    key, rows, cols, bg = key[order], rows[order], cols[order], bg[order]
    n = key.size
    label = np.arange(n)
    m = p.merge_px
    offsets = [(dr, dc) for dr in range(0, m + 1) for dc in range(-m, m + 1) if (dr, dc) > (0, 0)]
    src_list, dst_list = [], []
    for dr, dc in offsets:
        target = key + dr * width + dc
        pos = np.searchsorted(key, target)
        pos[pos >= n] = n - 1
        found = key[pos] == target
        src_list.append(np.nonzero(found)[0])
        dst_list.append(pos[found])
    src = np.concatenate(src_list) if src_list else np.empty(0, dtype=np.int64)
    dst = np.concatenate(dst_list) if dst_list else np.empty(0, dtype=np.int64)
    while src.size:  # min-label propagation with pointer jumping; converges in ~log(diameter) rounds
        lo = np.minimum(label[src], label[dst])
        before = label.copy()
        np.minimum.at(label, src, lo)
        np.minimum.at(label, dst, lo)
        label = label[label]
        if np.array_equal(before, label):
            break
    out: list[Detection] = []
    values = power[rows, cols].astype(np.float64)
    by_label = np.argsort(label, kind="stable")
    cuts = np.nonzero(np.diff(label[by_label]))[0] + 1
    for idx in np.split(by_label, cuts):
        area = idx.size
        if area < p.min_area or area > p.max_area:
            continue
        rr, cc = rows[idx], cols[idx]
        if max(rr.max() - rr.min(), cc.max() - cc.min()) + 1 > p.max_extent:
            continue
        j = idx[int(np.argmax(values[idx]))]
        peak = float(values[j])
        out.append(
            Detection(
                row=float(rr.mean()),
                col=float(cc.mean()),
                area_px=int(area),
                peak=peak,
                peak_db=10.0 * float(np.log10(peak)),
                background=float(bg[j]),
                contrast_db=10.0 * float(np.log10(peak / max(float(bg[j]), 1e-12))),
            )
        )
    out.sort(key=lambda d: (d.row, d.col))
    return out
