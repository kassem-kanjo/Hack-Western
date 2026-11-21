"""Property tests for the radar ship detector: planted ships are found, clean sea stays quiet."""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.sar_detect import DetectorParams, detect_ships, dilate

LOOKS = 4.4  # equivalent number of looks of Sentinel-1 GRDH


def sea(shape: tuple[int, int], seed: int, mean: float = 0.02) -> np.ndarray:
    """Speckled sea clutter: gamma-distributed power with the right look count."""
    rng = np.random.default_rng(seed)
    return rng.gamma(LOOKS, mean / LOOKS, size=shape)


def plant(img: np.ndarray, r: int, c: int, size: int = 4, gain: float = 40.0) -> None:
    """A ship: a size x size block ``gain`` times brighter than the sea."""
    img[r : r + size, c : c + size] *= gain
    img[r : r + size, c : c + size] += 0.02 * gain * 0.5


@settings(max_examples=15, deadline=None)
@given(seed=st.integers(0, 10_000), mean=st.floats(0.003, 0.08))
def test_clean_sea_has_almost_no_false_alarms(seed: int, mean: float) -> None:
    img = sea((300, 300), seed, mean)
    assert len(detect_ships(img)) <= 1


@settings(max_examples=15, deadline=None)
@given(seed=st.integers(0, 10_000), r=st.integers(80, 200), c=st.integers(80, 200))
def test_planted_ship_is_found_at_its_position(seed: int, r: int, c: int) -> None:
    img = sea((300, 300), seed)
    plant(img, r, c)
    found = detect_ships(img)
    near = [d for d in found if abs(d.row - (r + 1.5)) < 3 and abs(d.col - (c + 1.5)) < 3]
    assert len(near) == 1
    assert near[0].area_px >= 4 and near[0].contrast_db > 10


def test_recall_over_many_ships_is_high() -> None:
    img = sea((600, 600), 7)
    spots = [(60 + 100 * i, 60 + 100 * j) for i in range(5) for j in range(5)]
    for r, c in spots:
        plant(img, r, c, size=4, gain=25.0)
    found = detect_ships(img)
    hits = sum(any(abs(d.row - r - 1.5) < 3 and abs(d.col - c - 1.5) < 3 for d in found) for r, c in spots)
    assert hits >= 24
    assert len(found) - hits <= 1


def test_threshold_k_controls_sensitivity() -> None:
    img = sea((300, 300), 3)
    plant(img, 150, 150, size=4, gain=6.0)  # a weak ship
    assert len(detect_ships(img, params=DetectorParams(k=3.0, min_peak_ratio=3.0))) >= 1
    assert len(detect_ships(img, params=DetectorParams(k=30.0))) == 0


def test_land_mask_hides_targets_and_bright_land_is_ignored() -> None:
    img = sea((300, 300), 5)
    img[:, :100] = 0.5  # bright land on the left
    land = np.zeros(img.shape, dtype=bool)
    land[:, :100] = True
    plant(img, 150, 200)
    plant(img, 150, 20)  # a "ship" drawn on land: must be ignored
    found = detect_ships(img, land)
    assert len(found) == 1 and abs(found[0].col - 201.5) < 3
    # without the mask the land edge and its clutter do not become ships either (too big / too bright)
    assert all(d.area_px <= DetectorParams().max_area for d in detect_ships(img))


def test_size_filters_reject_speckle_and_huge_blobs() -> None:
    img = sea((300, 300), 11)
    img[100, 100] *= 200  # single hot pixel
    img[200:260, 200:260] *= 60  # 3600 px blob: a coastline or rig field, not a ship
    assert detect_ships(img) == []


def test_nodata_and_nan_are_ignored() -> None:
    img = sea((200, 200), 2)
    img[:, :50] = np.nan
    img[:, 50:60] = 0.0
    plant(img, 100, 120)
    found = detect_ships(img)
    assert len(found) == 1


def test_dilate_grows_by_radius() -> None:
    m = np.zeros((11, 11), dtype=bool)
    m[5, 5] = True
    d = dilate(m, 2)
    assert d.sum() == 25 and d[3:8, 3:8].all()
    assert (dilate(m, 0) == m).all()


@settings(max_examples=25, deadline=None)
@given(seed=st.integers(0, 1000), radius=st.integers(0, 6))
def test_dilate_matches_brute_force(seed: int, radius: int) -> None:
    rng = np.random.default_rng(seed)
    m = rng.random((23, 31)) > 0.95
    expected = np.zeros_like(m)
    for r, c in zip(*np.nonzero(m), strict=True):
        expected[max(r - radius, 0) : r + radius + 1, max(c - radius, 0) : c + radius + 1] = True
    assert (dilate(m, radius) == expected).all()


def test_bright_land_mask_marks_land_not_ships() -> None:
    from darkfleet.core.sar_detect import bright_land_mask

    img = sea((300, 300), 4)
    img[:, :80] = 0.3  # bright land, -5 dB
    plant(img, 150, 200, size=6, gain=60.0)
    mask = bright_land_mask(img)
    assert mask[20:-20, :60].all() and not mask[:, 120:].any()
