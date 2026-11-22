"""Properties of the satellite-check geometry and scene choice (issue #268)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.satcheck import (
    KM_PER_KN_HOUR,
    MIN_RADIUS_KM,
    RADIUS_CAP_KM,
    DarkWindow,
    choose_scene,
    minutes_after_switch_off,
    rank_dark_alerts,
    reach_area,
)

T0 = datetime(2026, 11, 21, 22, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=30)


def _alert(*, start: datetime, end: datetime | None, speed: float, lat: float, lon: float) -> dict:
    """A minimal dark alert in the watch.json shape."""
    return {
        "kind": "dark",
        "t_start": start.isoformat().replace("+00:00", "Z"),
        "t_end": end.isoformat().replace("+00:00", "Z") if end else None,
        "last": {"lat": lat, "lon": lon, "t": start.isoformat().replace("+00:00", "Z")},
        "speed_cap_kn": speed,
    }


@given(
    speed=st.floats(min_value=1.0, max_value=45.0),
    hours=st.floats(min_value=0.0, max_value=72.0),
    lat=st.floats(min_value=-70.0, max_value=70.0),
    lon=st.floats(min_value=-170.0, max_value=170.0),
)
@settings(max_examples=200, deadline=None)
def test_radius_is_reach_capped_and_floored(speed: float, hours: float, lat: float, lon: float) -> None:
    end = T0 + timedelta(hours=hours)
    alert = _alert(start=T0, end=end, speed=speed, lat=lat, lon=lon)
    area = reach_area(alert, NOW)
    reach = speed * KM_PER_KN_HOUR * ((end - T0).total_seconds() / 3600.0)  # same rounding as timedelta
    assert area.reach_km == pytest.approx(reach, rel=1e-9, abs=1e-9)
    assert area.radius_km <= RADIUS_CAP_KM + 1e-9
    assert area.radius_km >= MIN_RADIUS_KM - 1e-9
    assert area.capped == (reach > RADIUS_CAP_KM)
    if reach <= RADIUS_CAP_KM and reach >= MIN_RADIUS_KM:
        assert area.radius_km == pytest.approx(reach, rel=1e-9)


@given(
    lat=st.floats(min_value=-70.0, max_value=70.0),
    lon=st.floats(min_value=-170.0, max_value=170.0),
    radius_hours=st.floats(min_value=0.1, max_value=10.0),
)
@settings(max_examples=200, deadline=None)
def test_bbox_contains_centre_and_spans_radius(lat: float, lon: float, radius_hours: float) -> None:
    alert = _alert(start=T0, end=T0 + timedelta(hours=radius_hours), speed=16.0, lat=lat, lon=lon)
    area = reach_area(alert, NOW)
    west, south, east, north = area.bbox
    assert west < lon < east
    assert south < lat < north
    # The north-south edge is radius_km away: 1 degree of latitude is ~111.32 km.
    assert (north - south) / 2 * 111.32 == pytest.approx(area.radius_km, rel=1e-6)


def test_ongoing_window_grows_with_now() -> None:
    alert = _alert(start=T0, end=None, speed=6.0, lat=56.0, lon=20.0)  # 11.1 km/h
    early = reach_area(alert, T0 + timedelta(hours=1))
    later = reach_area(alert, T0 + timedelta(hours=2))
    assert later.reach_km == pytest.approx(2 * early.reach_km)


def test_reach_at_window_end_not_now_when_ended() -> None:
    alert = _alert(start=T0, end=T0 + timedelta(hours=1), speed=10.0, lat=56.0, lon=20.0)
    assert reach_area(alert, NOW).reach_km == pytest.approx(10.0 * KM_PER_KN_HOUR)


def _window(start: datetime, end: datetime | None) -> DarkWindow:
    return DarkWindow(start=start, end=end)


@given(
    offsets=st.lists(st.integers(min_value=-72 * 60, max_value=72 * 60), min_size=1, max_size=12),
    dark_minutes=st.integers(min_value=1, max_value=600),
    ongoing=st.booleans(),
)
@settings(max_examples=300, deadline=None)
def test_scene_choice_is_inside_or_nearest_edge(offsets: list[int], dark_minutes: int, ongoing: bool) -> None:
    window = _window(T0, None if ongoing else T0 + timedelta(minutes=dark_minutes))
    times = [T0 + timedelta(minutes=m) for m in offsets]
    choice = choose_scene(times, window, NOW)
    assert choice is not None  # the list is never empty here
    chosen = times[choice.index]
    end = window.effective_end(NOW)
    inside = [t for t in times if window.start <= t <= end]
    if inside:
        assert choice.relation == "during"
        assert window.start <= chosen <= end
        assert chosen == min(inside)  # the earliest pass inside the window
    else:
        assert choice.relation in ("before", "after")
        befores = [t for t in times if t < window.start]
        afters = [t for t in times if t > end]
        if choice.relation == "before":
            assert chosen == max(befores)
            if afters:
                gap_before = (window.start - chosen).total_seconds()
                gap_after = (min(afters) - end).total_seconds()
                assert gap_before <= gap_after
        else:
            assert chosen == min(afters)
            assert befores == [] or (min(afters) - end) < (window.start - max(befores))


def test_scene_choice_none_without_passes() -> None:
    assert choose_scene([], _window(T0, None), NOW) is None


def test_scene_choice_tie_goes_to_before() -> None:
    window = _window(T0, T0 + timedelta(hours=2))
    before = T0 - timedelta(hours=1)
    after = T0 + timedelta(hours=3)  # both one hour from the window edges
    choice = choose_scene([before, after], window, NOW)
    assert choice is not None and choice.relation == "before"


def test_minutes_after_switch_off_signed() -> None:
    window = _window(T0, None)
    assert minutes_after_switch_off(T0 + timedelta(minutes=170), window) == 170
    assert minutes_after_switch_off(T0 - timedelta(minutes=90), window) == -90


def test_rank_dark_alerts_orders_by_could_meet_then_severity() -> None:
    def alert(aid: str, met: int, severity: float, kind: str = "dark", area: str = "BALTIC_WATCH") -> dict:
        contacts = [{"could_meet": True}] * met + [{"could_meet": False}] * 3
        return {"id": aid, "kind": kind, "area_id": area, "severity": severity, "contacts": contacts}

    alerts = [
        alert("a", 1, 0.9),
        alert("b", 4, 0.1),
        alert("c", 4, 0.5),
        alert("d", 9, 1.0, kind="spoof"),
        alert("e", 9, 1.0, area="HORMUZ"),
    ]
    ranked = rank_dark_alerts(alerts, area_id="BALTIC_WATCH", limit=3)
    assert [a["id"] for a in ranked] == ["c", "b", "a"]
    assert rank_dark_alerts(alerts, limit=0) == []
