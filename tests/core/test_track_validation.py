"""Tests for the ground-truth harness on synthetic straight-line tracks.

A straight track is the one case where the right answer is known exactly: a ship that
steams at a constant speed along a geodesic must be inside its own prism at every hidden
fix, so any shortfall is a bug in the harness or the prism rather than in the data. All
randomness is seeded so a failure points at one formula.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from darkfleet.core.geo import GEOD, KNOT_KM_H, Fix
from darkfleet.core.track_validation import (
    bridge_auc,
    containment,
    detector_recall,
    false_positive_rate,
    inject_gaps,
    mask_segments,
)
from darkfleet.core.tracks import Event, Ping

START = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
LAT0, LON0 = 55.0, 10.0
MMSI = 211000001


def straight_track(
    hours: float = 24.0,
    step_min: float = 10.0,
    sog: float = 8.0,
    bearing: float = 90.0,
    mmsi: int = MMSI,
) -> list[Ping]:
    """Pings every ``step_min`` minutes along a geodesic, at constant speed and heading."""
    n = int(hours * 60.0 / step_min) + 1
    pings: list[Ping] = []
    for k in range(n):
        dist_m = sog * KNOT_KM_H * (k * step_min / 60.0) * 1000.0
        lon, lat, _ = GEOD.fwd(LON0, LAT0, bearing, dist_m)
        pings.append(
            Ping(
                mmsi=mmsi,
                t=START + timedelta(minutes=k * step_min),
                lat=float(lat),
                lon=float(lon),
                sog=sog,
            )
        )
    return pings


@dataclass(frozen=True)
class ClassedTrack:
    """Minimal stand-in for the track-plus-class object the script passes in."""

    vessel_class: str
    pings: tuple[Ping, ...]


def event(kind: str, start: datetime, end: datetime, mmsi: int = MMSI) -> Event:
    return Event(
        kind=kind,  # type: ignore[arg-type]
        mmsi=mmsi,
        start=start,
        end=end,
        lat=0.0,
        lon=0.0,
        severity=0.5,
        explanation="synthetic test event",
    )


def test_mask_segments_are_underway_windows_with_bracketed_truths() -> None:
    track = straight_track(hours=24.0, step_min=10.0)
    cases = mask_segments(track, np.random.default_rng(7), min_h=1, max_h=6)
    assert cases
    for case in cases:
        hours = (case.b.t - case.a.t).total_seconds() / 3600.0
        assert 1.0 - 1e-9 <= hours <= 6.0 + 1e-9
        assert case.truths
        assert all(case.a.t < truth.t < case.b.t for truth in case.truths)
        # the fixes inside the window are consecutive, so their order follows time
        assert [truth.t for truth in case.truths] == sorted(truth.t for truth in case.truths)
        assert case.vessel_class == "default"


def test_mask_segments_needs_underway_speed() -> None:
    parked = straight_track(hours=12.0)
    slow = [Ping(p.mmsi, p.t, p.lat, p.lon, sog=0.5) for p in parked]
    assert mask_segments(slow, np.random.default_rng(7)) == []
    assert mask_segments(parked[:2], np.random.default_rng(7)) == []


def test_mask_segments_reads_class_from_classed_track() -> None:
    classed = ClassedTrack("cargo", tuple(straight_track(hours=12.0)))
    cases = mask_segments(classed, np.random.default_rng(7))
    assert cases
    assert {case.vessel_class for case in cases} == {"cargo"}


def test_containment_is_complete_on_straight_lines() -> None:
    track = straight_track(hours=24.0, step_min=5.0, sog=8.0)
    cases = mask_segments(track, np.random.default_rng(7), min_h=1, max_h=8)
    assert len(cases) >= 3

    out = containment(cases, {"default": 20.0})

    assert out["n"] == len(cases)
    assert out["inside_footprint"] == out["n"]
    assert out["inside_lens_at_truth_time"] == out["n"]
    assert out["rate"] == 1.0
    lo, hi = out["wilson_ci"]
    assert 0.0 <= lo <= 1.0 and hi == 1.0


def test_containment_fails_when_cap_is_below_track_speed() -> None:
    cases = mask_segments(straight_track(hours=12.0, sog=15.0), np.random.default_rng(7))
    out = containment(cases, {"default": 1.0})
    assert out["n"] > 0
    assert out["inside_footprint"] == 0
    assert out["inside_lens_at_truth_time"] == 0
    assert out["rate"] == 0.0


def test_containment_uses_class_cap_and_default_fallback() -> None:
    classed = ClassedTrack("tanker", tuple(straight_track(hours=12.0, sog=8.0)))
    cases = mask_segments(classed, np.random.default_rng(7))
    assert containment(cases, {"tanker": 20.0})["rate"] == 1.0
    # unknown class falls back to "default" rather than dropping the case
    assert containment(cases, {"default": 20.0})["rate"] == 1.0
    with pytest.raises(KeyError):
        containment(cases, {"cargo": 20.0})


def test_bridge_auc_separates_true_path_from_displaced_decoys() -> None:
    cases = mask_segments(straight_track(hours=24.0, step_min=10.0), np.random.default_rng(7))
    assert cases

    out = bridge_auc(cases, sigma=5.0, rng=np.random.default_rng(7), n_decoys=10)

    assert out["n_cases"] == len(cases)
    assert out["auc"] > 0.9
    lo, hi = out["ci"]
    assert lo > 0.5
    assert lo <= out["auc"] <= hi


def test_bridge_auc_empty_cases_is_nan() -> None:
    out = bridge_auc([], sigma=5.0, rng=np.random.default_rng(7))
    assert math.isnan(out["auc"])
    assert out["n_cases"] == 0


def test_inject_gaps_is_deterministic_and_disjoint() -> None:
    track = straight_track(hours=72.0, step_min=5.0)
    first_kept, first_gaps = inject_gaps(track, np.random.default_rng(7), 3)
    second_kept, second_gaps = inject_gaps(track, np.random.default_rng(7), 3)

    assert first_gaps == second_gaps
    assert first_kept == second_kept
    assert len(first_gaps) == 3
    assert len(first_kept) < len(track)
    # a gap is bracketed by surviving pings and the gaps do not touch
    kept_times = {p.t for p in first_kept}
    for start, end in first_gaps:
        assert start in kept_times and end in kept_times
        assert start < end
    ordered = sorted(first_gaps)
    assert all(ordered[i][1] < ordered[i + 1][0] for i in range(len(ordered) - 1))


def test_inject_gaps_short_track_returns_untouched() -> None:
    track = straight_track(hours=0.5, step_min=10.0)
    kept, gaps = inject_gaps(track, np.random.default_rng(7), 3)
    assert gaps == []
    assert kept == track


def test_detector_recall_with_a_perfect_detector_is_one() -> None:
    track = straight_track(hours=72.0, step_min=5.0)
    kept, gaps = inject_gaps(track, np.random.default_rng(7), 3)
    assert len(gaps) == 3

    def perfect(pings: object, coverage: object) -> list[Event]:
        return [event("silence", start, end) for start, end in gaps]

    out = detector_recall([(kept, gaps)], [], perfect)

    assert out["tracks"] == 1
    assert out["injected"] == 3
    assert out["found"] == 3
    assert out["recall"] == 1.0


def test_detector_recall_ignores_unrelated_events() -> None:
    track = straight_track(hours=48.0, step_min=5.0)
    kept, gaps = inject_gaps(track, np.random.default_rng(7), 2)
    assert gaps

    def unrelated(pings: object, coverage: object) -> list[Event]:
        # a detector that reports the same length of silence one week later
        return [event("silence", start + timedelta(days=7), end + timedelta(days=7)) for start, end in gaps]

    out = detector_recall([(kept, gaps)], [], unrelated)

    assert out["injected"] == 2
    assert out["found"] == 0
    assert out["recall"] == 0.0


def test_detector_recall_with_nothing_injected_is_nan() -> None:
    out = detector_recall([(straight_track(hours=1.0), [])], [], lambda pings, coverage: [])
    assert out["injected"] == 0
    assert out["found"] == 0
    assert math.isnan(out["recall"])


def test_false_positive_rate_is_events_per_vessel_day() -> None:
    track = straight_track(hours=24.0)

    def noisy(pings: list[Ping]) -> list[Event]:
        return [event("jump", pings[0].t, pings[0].t), event("clone", pings[-1].t, pings[-1].t)]

    out = false_positive_rate([track, track], noisy)

    assert out["tracks"] == 2
    assert out["events"] == 4
    assert out["vessel_days"] == pytest.approx(2.0)
    assert out["per_vessel_day"] == pytest.approx(2.0)


def test_false_positive_rate_with_no_exposure_is_nan() -> None:
    def quiet(pings: list[Ping]) -> list[Event]:
        return []

    out = false_positive_rate([], quiet)
    assert out["tracks"] == 0
    assert out["events"] == 0
    assert math.isnan(out["per_vessel_day"])


def test_mask_cases_record_the_window_endpoints() -> None:
    """The two surviving fixes must be actual pings, not interpolated points."""
    track = straight_track(hours=12.0, step_min=10.0)
    times = {p.t for p in track}
    cases = mask_segments(track, np.random.default_rng(7))
    for case in cases:
        assert case.a.t in times and case.b.t in times
        assert isinstance(case.a, Fix)
