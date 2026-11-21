"""Tests for the ship-to-ship meeting detectors (issue #75)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from shapely.geometry import box

from darkfleet.core.encounters import dark_meetings, meetings
from darkfleet.core.geo import GEOD, KNOT_KM_H
from darkfleet.core.tracks import Event, Ping

T0 = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
LAT, LON = 59.0, 21.0  # open water in the Baltic, far from the test land polygons


def _track(
    mmsi: int,
    minutes: int,
    *,
    lat: float = LAT,
    lon: float = LON,
    sog: float = 1.0,
    step_min: int = 5,
) -> list[Ping]:
    """A track reporting every `step_min` minutes for `minutes` minutes (inclusive ends)."""
    return [Ping(mmsi, T0 + timedelta(minutes=m), lat, lon, sog) for m in range(0, minutes + 1, step_min)]


def test_two_vessels_drifting_together_are_one_meeting() -> None:
    tracks = {
        222: _track(222, 45, lon=LON + 0.001),  # ~57 m east of the other
        111: _track(111, 45),
    }

    events = meetings(tracks)

    assert len(events) == 1
    event = events[0]
    assert event.kind == "meeting"
    assert (event.mmsi, event.other_mmsi) == (111, 222)  # low MMSI first, regardless of dict order
    assert event.start == T0
    assert event.end == T0 + timedelta(minutes=45)
    assert event.evidence["minutes"] == pytest.approx(45.0)
    assert 0.0 <= event.evidence["min_distance_m"] <= 100.0
    assert event.evidence["mean_sog"] == pytest.approx(1.0)
    assert 0.0 < event.severity <= 1.0


def test_fast_vessels_are_not_a_meeting() -> None:
    tracks = {
        111: _track(111, 60, sog=12.0),
        222: _track(222, 60, lon=LON + 0.001, sog=12.0),
    }

    assert meetings(tracks) == []


def test_short_rendezvous_is_not_a_meeting() -> None:
    tracks = {111: _track(111, 20), 222: _track(222, 20, lon=LON + 0.001)}

    assert meetings(tracks) == []


def test_meeting_near_land_is_skipped() -> None:
    tracks = {111: _track(111, 45), 222: _track(222, 45, lon=LON + 0.001)}
    assert len(meetings(tracks)) == 1  # control: no land supplied

    one_km_away = box(LON - 0.2, LAT + 0.009, LON + 0.2, LAT + 0.2)  # ~1 km north
    assert meetings(tracks, land=one_km_away) == []

    eleven_km_away = box(LON - 0.2, LAT + 0.1, LON + 0.2, LAT + 0.3)
    assert len(meetings(tracks, land=eleven_km_away)) == 1  # far land does not hide the meeting


def test_pair_across_a_grid_cell_boundary_is_found() -> None:
    # 21.0049 and 21.0051 fall in different 0.01-degree cells; only the neighbour lookup connects them.
    tracks = {
        111: _track(111, 45, lon=21.0049),
        222: _track(222, 45, lon=21.0051),
    }

    assert len(meetings(tracks)) == 1


def test_wider_max_m_widens_the_cell_neighbourhood() -> None:
    # ~1.2 km apart at 59 N, two longitude cells apart: a fixed 3x3 lookup would never compare
    # them, but sizing the neighbourhood from max_m does.
    tracks = {111: _track(111, 45, lon=21.0), 222: _track(222, 45, lon=21.021)}

    assert meetings(tracks, max_m=700) == []  # genuinely too far for this separation
    assert len(meetings(tracks, max_m=1500)) == 1


def _crossing_pings(mmsi: int, heading: float, *, hours: float = 10.0, knots: float = 15.0) -> list[Ping]:
    """Two pings bracketing a gap that crosses (LAT, LON) after `hours`, travelling at `knots`."""
    reach_m = knots * KNOT_KM_H * hours * 1000.0
    lon0, lat0, _ = GEOD.fwd(LON, LAT, (heading + 180.0) % 360.0, reach_m)
    lon1, lat1, _ = GEOD.fwd(LON, LAT, heading, reach_m)
    return [
        Ping(mmsi, T0, lat0, lon0, knots),
        Ping(mmsi, T0 + timedelta(hours=2.0 * hours), lat1, lon1, knots),
    ]


def _silence(mmsi: int, pings: list[Ping]) -> Event:
    return Event(
        kind="silence",
        mmsi=mmsi,
        start=pings[0].t,
        end=pings[-1].t,
        lat=pings[0].lat,
        lon=pings[0].lon,
        severity=0.5,
        explanation="test silence",
    )


def test_dark_meeting_crossing_case_is_one_event() -> None:
    pings_a = _crossing_pings(111, 45.0)
    pings_b = _crossing_pings(222, 135.0)
    silences = [_silence(111, pings_a), _silence(222, pings_b)]

    events = dark_meetings(silences, {111: pings_a, 222: pings_b}, {111: 20.0, 222: 20.0})

    assert len(events) == 1
    event = events[0]
    assert event.kind == "dark_meeting"
    assert (event.mmsi, event.other_mmsi) == (111, 222)
    meet = T0 + timedelta(hours=10)
    assert event.start <= meet <= event.end
    assert event.evidence["windows"] >= 1
    assert event.evidence["max_overlap_km2"] > 0
    assert event.evidence["minutes"] >= 0
    assert 0.0 < event.severity <= 1.0
