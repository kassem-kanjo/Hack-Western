"""Tests for issue #73: track splitting, coverage, silences, jumps and clones.

The synthetic tracks are deliberately small and hand-checkable: each test pins one rule and one
threshold, so a regression points at a formula rather than at the detector. The Hypothesis test
covers the property the jump rule must never violate: physics below the cap can never be a jump.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from darkfleet.core.geo import GEOD, KNOT_KM_H, distance_km
from darkfleet.core.kinematics import clones, covered, jumps, silences, split_tracks
from darkfleet.core.speeds import max_speed_knots
from darkfleet.core.tracks import CoverageMinute, Ping

T0 = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
A = (55.0, 12.0)
B = (55.0, 12.6)  # about 38 km east of A at this latitude


def _ping(
    minutes: float = 0.0,
    *,
    mmsi: int = 1,
    lat: float = 55.0,
    lon: float = 12.0,
    sog: float | None = 10.0,
) -> Ping:
    return Ping(mmsi=mmsi, t=T0 + timedelta(minutes=minutes), lat=lat, lon=lon, sog=sog)


def _coverage(t0: datetime, t1: datetime, n_msgs: int = 200) -> list[CoverageMinute]:
    """A healthy receiver: one row per UTC minute touched by [t0, t1], inclusive."""
    out: list[CoverageMinute] = []
    minute = t0.replace(second=0, microsecond=0)
    end = t1.replace(second=0, microsecond=0)
    while minute <= end:
        out.append(CoverageMinute(minute=minute, n_msgs=n_msgs))
        minute += timedelta(minutes=1)
    return out


def _clone_track(minutes: list[int], places: list[tuple[float, float]]) -> list[Ping]:
    return [
        Ping(mmsi=1, t=T0 + timedelta(minutes=m), lat=lat, lon=lon, sog=8.0)
        for m, (lat, lon) in zip(minutes, places, strict=True)
    ]


# --- split_tracks -------------------------------------------------------------------------


def test_split_drops_junk_duplicates_and_sorts_each_mmsi() -> None:
    early = _ping(minutes=0, lat=55.1, lon=12.1)
    late = _ping(minutes=10, lat=55.0, lon=12.0)
    other = _ping(minutes=5, mmsi=2, lat=56.0, lon=13.0)
    junk = [
        _ping(minutes=1, lat=0.0, lon=0.0),
        _ping(minutes=2, lat=91.0, lon=12.0),
        _ping(minutes=3, lat=-91.0, lon=12.0),
        _ping(minutes=4, lat=55.0, lon=181.0),
        _ping(minutes=5, lat=55.0, lon=-181.0),
    ]
    tracks = split_tracks([late, *junk, early, other, late])

    assert list(tracks) == [1, 2]
    assert tracks[1] == [early, late]  # exact duplicate dropped, order restored
    assert tracks[2] == [other]


def test_split_keeps_equator_and_valid_boundaries() -> None:
    pings = [_ping(minutes=0, lat=0.0, lon=10.0), _ping(minutes=1, lat=90.0, lon=180.0)]
    assert len(split_tracks(pings)[1]) == 2


# --- covered ------------------------------------------------------------------------------


def test_covered_needs_every_minute_and_enough_traffic() -> None:
    t1 = T0 + timedelta(minutes=5)
    full = _coverage(T0, t1)
    assert covered(T0, t1, full)

    hole = [c for c in full if c.minute != T0 + timedelta(minutes=3)]
    assert not covered(T0, t1, hole)

    low = [CoverageMinute(c.minute, 149) if c.minute == T0 + timedelta(minutes=2) else c for c in full]
    assert not covered(T0, t1, low)


def test_covered_counts_absent_minute_as_zero() -> None:
    assert covered(T0, T0, [CoverageMinute(T0, 150)])
    assert not covered(T0, T0, [CoverageMinute(T0, 149)])
    assert not covered(T0, T0, [])
    assert not covered(T0, T0 - timedelta(minutes=1), [])  # backwards range is not coverage


# --- silences -----------------------------------------------------------------------------


def test_silence_underway_with_full_coverage() -> None:
    track = [_ping(minutes=0, sog=11.0), _ping(minutes=134, lat=55.3, sog=11.0)]
    events = silences(track, _coverage(track[0].t, track[1].t))

    assert len(events) == 1
    event = events[0]
    assert event.kind == "silence"
    assert event.mmsi == 1
    assert event.start == track[0].t
    assert event.end == track[1].t
    assert event.evidence["minutes"] == pytest.approx(134.0)
    assert event.evidence["coverage_min_msgs"] == 200
    assert event.explanation.startswith("Silent for 2 h 14 min while underway at 11 kn")
    assert 0.0 < event.severity <= 1.0


def test_gap_at_or_below_threshold_is_not_a_silence() -> None:
    track = [_ping(minutes=0), _ping(minutes=60)]
    assert silences(track, _coverage(track[0].t, track[1].t)) == []


def test_berth_never_makes_a_silence() -> None:
    track = [_ping(minutes=0, sog=0.4), _ping(minutes=180, lat=55.3)]
    coverage = _coverage(track[0].t, track[1].t)
    assert silences(track, coverage) == []
    # Even a validation sweep that sets min_sog to zero must not call a berthed ship dark.
    assert silences(track, coverage, min_sog=0.0) == []


def test_unknown_speed_is_not_underway() -> None:
    track = [_ping(minutes=0, sog=None), _ping(minutes=180, lat=55.3)]
    assert silences(track, _coverage(track[0].t, track[1].t)) == []


def test_coverage_hole_suppresses_a_silence() -> None:
    track = [_ping(minutes=0, sog=10.0), _ping(minutes=180, lat=55.3, sog=10.0)]
    coverage = _coverage(track[0].t, track[1].t)

    holed = [c for c in coverage if c.minute != T0 + timedelta(minutes=90)]
    assert silences(track, holed) == []

    low = [CoverageMinute(c.minute, 149) if c.minute == T0 + timedelta(minutes=90) else c for c in coverage]
    assert silences(track, low) == []


# --- jumps --------------------------------------------------------------------------------


def test_impossible_jump_is_flagged_and_maxes_severity() -> None:
    cap = max_speed_knots("tanker")
    track = [_ping(minutes=0), _ping(minutes=10, lat=55.0, lon=13.9)]

    events = jumps(track, cap)

    assert len(events) == 1
    event = events[0]
    assert event.kind == "jump"
    assert event.start == track[0].t
    assert event.end == track[1].t
    assert event.lat == pytest.approx(55.0)  # marks the impossible fix
    assert event.evidence["km"] > 100
    assert event.evidence["implied_kn"] > 3 * cap
    assert event.evidence["cap_kn"] == pytest.approx(round(cap, 2))
    assert event.severity == 1.0


def test_plausible_speed_is_not_a_jump() -> None:
    cap = max_speed_knots("cargo")
    track = [_ping(minutes=0), _ping(minutes=60, lat=55.0, lon=12.15)]
    assert jumps(track, cap) == []


def test_short_hop_is_ignored_even_at_absurd_speed() -> None:
    # ~3.3 km in one second is thousands of knots, but below the 5 km noise floor.
    track = [
        _ping(minutes=0),
        Ping(mmsi=1, t=T0 + timedelta(seconds=1), lat=55.03, lon=12.0, sog=10.0),
    ]
    assert jumps(track, cap_kn=1.0) == []


def test_same_time_pings_are_left_to_clone_detection() -> None:
    track = [
        _ping(minutes=0),
        Ping(mmsi=1, t=T0, lat=56.0, lon=12.0, sog=10.0),
    ]
    assert jumps(track, cap_kn=1.0) == []


# --- clones -------------------------------------------------------------------------------


def test_clone_detects_alternation_between_two_clusters() -> None:
    track = _clone_track([0, 1, 2, 3], [A, B, A, B])
    events = clones(track)

    assert len(events) == 1
    event = events[0]
    assert event.kind == "clone"
    assert event.evidence["flips"] == 3
    assert event.evidence["max_separation_km"] > 30
    assert event.start == T0
    assert event.end == T0 + timedelta(minutes=3)
    assert "two transmitters" in event.explanation
    assert 0.0 < event.severity <= 1.0


def test_two_flips_are_not_enough() -> None:
    assert clones(_clone_track([0, 1, 2], [A, B, A])) == []


def test_positions_closer_than_min_km_are_not_a_clone() -> None:
    near = (55.0, 12.3)  # ~19 km from A
    assert clones(_clone_track([0, 1, 2, 3], [A, near, A, near])) == []


def test_alternation_slower_than_the_window_is_not_a_clone() -> None:
    assert clones(_clone_track([0, 6, 12, 18], [A, B, A, B])) == []


def test_two_separate_episodes_give_two_events() -> None:
    track = _clone_track([0, 1, 2, 3, 60, 61, 62, 63], [A, B, A, B, A, B, A, B])
    events = clones(track)

    assert len(events) == 2
    assert events[0].start == T0
    assert events[1].start == T0 + timedelta(minutes=60)


def test_long_continuous_episode_gives_one_event() -> None:
    minutes = list(range(9))
    places = [A if i % 2 == 0 else B for i in minutes]
    events = clones(_clone_track(minutes, places))

    assert len(events) == 1
    assert events[0].evidence["flips"] == 8


def test_three_distinct_locations_are_not_two_clusters() -> None:
    far_third = (55.0, 13.2)  # far from B as well as from A
    assert clones(_clone_track([0, 1, 2, 3], [A, B, A, far_third])) == []


# --- property: below the cap, never a jump ------------------------------------------------


@st.composite
def _tracks_below_cap(draw) -> tuple[list[Ping], float]:
    lat = draw(st.floats(54.0, 56.0))
    lon = draw(st.floats(10.0, 14.0))
    cap = draw(st.floats(5.0, 25.0))
    count = draw(st.integers(2, 8))
    pings = [Ping(mmsi=1, t=T0, lat=lat, lon=lon, sog=10.0)]
    t = T0
    for _ in range(count - 1):
        dt_min = draw(st.floats(0.5, 60.0))
        heading = draw(st.floats(0.0, 360.0))
        reach_km = cap * KNOT_KM_H * dt_min / 60.0
        dist_km = reach_km * draw(st.floats(0.0, 0.95))  # stay strictly below the cap
        lon, lat, _ = GEOD.fwd(lon, lat, heading, dist_km * 1000.0)
        t += timedelta(minutes=dt_min)
        pings.append(Ping(mmsi=1, t=t, lat=float(lat), lon=float(lon), sog=10.0))
    return pings, cap


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_tracks_below_cap())
def test_no_jumps_when_every_implied_speed_is_below_cap(generated: tuple[list[Ping], float]) -> None:
    track, cap = generated
    implied = [
        distance_km(a.lat, a.lon, b.lat, b.lon) / ((b.t - a.t).total_seconds() / 3600.0) / KNOT_KM_H
        for a, b in pairwise(track)
    ]
    assert max(implied) <= cap * 1.000001
    assert jumps(track, cap) == []
