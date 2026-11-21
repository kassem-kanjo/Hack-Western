"""Tests for issue #86: open silences, reach, and possible contacts on the watch screen.

The deterministic tests pin the rules that decide whether a ship is drawn as ongoing-dark and
which other hulls are worth showing next to it. The Hypothesis properties cover the
monotonicity and containment guarantees the UI leans on: the reach circle may only grow with
time, a fix is always inside its own reach area, a stopped hull sitting on the last fix is
always a candidate meeting, and no ship further than the cap allows is ever a contact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from shapely.geometry import box

from darkfleet.core.areas import LISTENING
from darkfleet.core.geo import GEOD, Fix
from darkfleet.core.kinematics import build_activity_index
from darkfleet.core.tracks import CoverageMinute, Ping
from darkfleet.core.watch import (
    Contact,
    contact_could_meet,
    dark_type_allowed,
    effective_cap_kn,
    in_reach,
    left_coverage,
    offshore_km,
    open_silences,
    possible_contacts,
    reach_km,
    valid_mmsi,
)

T0 = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
MMSI = 211000001  # German MID 211: a real-looking identity the open-silence gate accepts.
OTHER_MMSIS = (211000002, 211000003)
SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _coverage(t0: datetime, t1: datetime, n_msgs: int = 200) -> list[CoverageMinute]:
    """A healthy receiver: one row per UTC minute touched by [t0, t1], inclusive."""
    out: list[CoverageMinute] = []
    minute = t0.replace(second=0, microsecond=0)
    end = t1.replace(second=0, microsecond=0)
    while minute <= end:
        out.append(CoverageMinute(minute=minute, n_msgs=n_msgs))
        minute += timedelta(minutes=1)
    return out


def _ping(
    minutes: float = 0.0,
    *,
    mmsi: int = MMSI,
    lat: float = 55.0,
    lon: float = 12.0,
    sog: float | None = 10.0,
    cog: float | None = None,
    heading: int | None = None,
) -> Ping:
    return Ping(
        mmsi=mmsi,
        t=T0 + timedelta(minutes=minutes),
        lat=lat,
        lon=lon,
        sog=sog,
        cog=cog,
        heading=heading,
    )


def _point(bearing_deg: float, km: float) -> tuple[float, float]:
    """Point `km` from the equator at 0E along `bearing_deg`: a hand-checkable synthetic fix."""
    lon, lat, _ = GEOD.fwd(0.0, 0.0, bearing_deg, km * 1000.0)
    return lat, lon


# --- dark_type_allowed --------------------------------------------------------------------


@pytest.mark.parametrize("ship_type", [70, 75, 79, 80, 85, 89, None, 0])
def test_dark_alerts_allow_cargo_tanker_and_unknown_types(ship_type: int | None) -> None:
    assert dark_type_allowed(ship_type)


@pytest.mark.parametrize("ship_type", [30, 37, 40, 49, 50, 52, 60, 63, 69, 90, 99])
def test_dark_alerts_reject_every_other_type(ship_type: int) -> None:
    # Passenger ferries, high-speed craft, tugs/pilots, fishing, service and pleasure craft:
    # the real 12 h recording's alert list was dominated by these.
    assert not dark_type_allowed(ship_type)


# --- reach_km -----------------------------------------------------------------------------


@given(
    cap=st.floats(0.0, 40.0, allow_nan=False, allow_infinity=False),
    h1=st.floats(0.0, 240.0, allow_nan=False, allow_infinity=False),
    h2=st.floats(0.0, 240.0, allow_nan=False, allow_infinity=False),
)
@SETTINGS
def test_reach_radius_is_monotonic_in_time(cap: float, h1: float, h2: float) -> None:
    assume(h1 <= h2)
    early = reach_km(cap, T0, T0 + timedelta(hours=h1))
    late = reach_km(cap, T0, T0 + timedelta(hours=h2))

    assert early <= late + 1e-9
    assert reach_km(cap, T0, T0) == 0.0
    assert reach_km(cap, T0, T0 - timedelta(hours=1)) == 0.0


def test_reach_km_matches_the_contract_formula() -> None:
    # 10 kn for half an hour is 5 nm, 9.26 km.
    assert reach_km(10.0, T0, T0 + timedelta(minutes=30)) == pytest.approx(9.26, abs=0.01)


# --- in_reach -----------------------------------------------------------------------------


@given(
    lat=st.floats(-80.0, 80.0, allow_nan=False, allow_infinity=False),
    lon=st.floats(-180.0, 180.0, allow_nan=False, allow_infinity=False),
    cap=st.floats(0.1, 30.0, allow_nan=False, allow_infinity=False),
    gap_h=st.floats(0.5, 48.0, allow_nan=False, allow_infinity=False),
    azimuth=st.floats(0.0, 360.0, allow_nan=False, allow_infinity=False),
    fraction=st.floats(0.0, 0.9, allow_nan=False, allow_infinity=False),
)
@SETTINGS
def test_in_reach_is_true_at_the_last_fix(
    lat: float, lon: float, cap: float, gap_h: float, azimuth: float, fraction: float
) -> None:
    last = Fix(lat=lat, lon=lon, t=T0)
    reach_m = reach_km(cap, T0, T0 + timedelta(hours=gap_h)) * 1000.0
    next_lon, next_lat, _ = GEOD.fwd(lon, lat, azimuth, reach_m * fraction)
    nxt = Fix(lat=float(next_lat), lon=float(next_lon), t=T0 + timedelta(hours=gap_h))

    # At its own fix the ship is trivially in reach, ongoing or closed.
    assert in_reach(last, None, cap, lat, lon, T0)
    assert in_reach(last, nxt, cap, lat, lon, T0)


def test_in_reach_disk_grows_until_the_next_fix_exists() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    lat, lon = _point(90.0, 30.0)

    assert not in_reach(last, None, 10.0, lat, lon, T0 + timedelta(minutes=30))
    assert in_reach(last, None, 10.0, lat, lon, T0 + timedelta(hours=2))
    # Before the last fix there is no reach area to be in.
    assert not in_reach(last, None, 10.0, lat, lon, T0 - timedelta(minutes=1))


def test_in_reach_uses_the_lens_once_a_next_fix_exists() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    next_lat, next_lon = _point(90.0, 20.0)
    nxt = Fix(lat=next_lat, lon=next_lon, t=T0 + timedelta(hours=2))
    behind_lat, behind_lon = _point(270.0, 36.0)

    assert in_reach(last, None, 20.0, behind_lat, behind_lon, T0 + timedelta(hours=1))
    # 36 km west is inside the disk from the last fix but cannot reach the next fix in time.
    assert not in_reach(last, nxt, 20.0, behind_lat, behind_lon, T0 + timedelta(hours=1))


def test_in_reach_is_antimeridian_safe() -> None:
    last = Fix(lat=0.0, lon=179.95, t=T0)

    # 0.1 degrees east of 179.95 is -179.95, about 11 km away.
    assert in_reach(last, None, 10.0, 0.0, -179.95, T0 + timedelta(minutes=45))
    assert not in_reach(last, None, 10.0, 0.0, -179.0, T0 + timedelta(minutes=45))


# --- open_silences ------------------------------------------------------------------------


def test_open_silence_started_underway_and_still_missing() -> None:
    track = [_ping(minutes=0, sog=11.0)]
    now = T0 + timedelta(minutes=134)
    events = open_silences({MMSI: track}, _coverage(T0, now), now)

    assert len(events) == 1
    event = events[0]
    assert event.kind == "silence"
    assert event.mmsi == MMSI
    assert event.start == T0
    assert event.end is None  # pipeline derives status "ongoing"
    assert event.lat == pytest.approx(55.0)
    assert event.evidence["minutes"] == pytest.approx(134.0)
    assert event.evidence["coverage_min_msgs"] == 200
    assert event.evidence["ongoing"] is True
    assert "has not reported since" in event.explanation
    assert 0.0 < event.severity <= 1.0


def test_open_silence_uses_the_latest_ping_even_if_the_track_is_unsorted() -> None:
    early = _ping(minutes=0, sog=11.0)
    late = _ping(minutes=30, sog=11.0)
    now = T0 + timedelta(minutes=200)

    events = open_silences({MMSI: [late, early]}, _coverage(T0, now), now)

    assert len(events) == 1
    assert events[0].start == late.t


def test_recent_ping_is_not_an_open_silence() -> None:
    now = T0 + timedelta(minutes=60)
    track = [_ping(minutes=0, sog=11.0)]

    assert open_silences({MMSI: track}, _coverage(T0, now), now) == []


@given(
    gap_min=st.floats(61.0, 300.0, allow_nan=False, allow_infinity=False),
    sog=st.floats(0.0, 0.49, allow_nan=False, allow_infinity=False),
)
@SETTINGS
def test_open_silences_ignores_moored_ships(gap_min: float, sog: float) -> None:
    now = T0 + timedelta(minutes=gap_min)
    track = [_ping(minutes=0, sog=sog)]

    assert open_silences({MMSI: track}, _coverage(T0, now), now) == []
    # Even a sweep that sets min_sog to zero must keep the berth floor.
    assert open_silences({MMSI: track}, _coverage(T0, now), now, min_sog=0.0) == []


@given(gap_min=st.floats(61.0, 300.0, allow_nan=False, allow_infinity=False))
@SETTINGS
def test_open_silences_ignores_a_quiet_area(gap_min: float) -> None:
    now = T0 + timedelta(minutes=gap_min)
    track = [_ping(minutes=0, sog=11.0)]

    assert open_silences({MMSI: track}, [], now) == []
    holed = [row for row in _coverage(T0, now) if row.minute != T0 + timedelta(minutes=30)]
    assert open_silences({MMSI: track}, holed, now) == []


def test_open_silence_is_suppressed_when_the_local_area_is_empty() -> None:
    track = [_ping(minutes=0, sog=11.0)]
    now = T0 + timedelta(minutes=134)
    index = build_activity_index({MMSI: track})

    assert open_silences({MMSI: track}, _coverage(T0, now), now, activity=index) == []


def test_open_silence_survives_when_receivers_nearby_keep_hearing_others() -> None:
    track = [_ping(minutes=0, sog=11.0)]
    now = T0 + timedelta(minutes=134)
    neighbours = {
        mmsi: [_ping(minutes=m, mmsi=mmsi, lat=55.05, lon=12.05, sog=8.0) for m in range(0, 135, 10)]
        for mmsi in OTHER_MMSIS
    }
    index = build_activity_index({MMSI: track, **neighbours})

    events = open_silences({MMSI: track, **neighbours}, _coverage(T0, now), now, activity=index)

    assert len(events) == 1
    assert events[0].evidence["local_other_vessels"] >= 2
    assert "receivers within 60 km kept hearing" in events[0].explanation


# --- left_coverage ------------------------------------------------------------------------


def test_left_coverage_is_true_at_a_box_edge_heading_outward() -> None:
    bbox = [0.0, 0.0, 5.0, 5.0]

    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=2.5, lon=0.0, cog=270.0), bbox)
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=2.5, lon=5.0, cog=90.0), bbox)
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=0.0, lon=2.5, cog=180.0), bbox)
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=5.0, lon=2.5, cog=0.0), bbox)


def test_left_coverage_falls_back_to_heading_and_needs_some_course() -> None:
    bbox = [0.0, 0.0, 5.0, 5.0]
    west_edge = {"mmsi": MMSI, "t": T0, "lat": 2.5, "lon": 0.0}

    assert left_coverage(Ping(**west_edge, heading=270), bbox)
    assert left_coverage(Ping(**west_edge, cog=250.0, heading=90), bbox)  # cog wins over heading
    assert not left_coverage(Ping(**west_edge), bbox)
    # Pointing back into the box is a coverage gap, not a coverage exit.
    assert not left_coverage(Ping(**west_edge, cog=90.0), bbox)
    # Deeper than 25 km from every edge.
    assert not left_coverage(Ping(mmsi=MMSI, t=T0, lat=2.5, lon=2.5, cog=270.0), bbox)
    # Already outside the box is not an edge exit either.
    assert not left_coverage(Ping(mmsi=MMSI, t=T0, lat=2.5, lon=-0.5, cog=270.0), bbox)


def test_left_coverage_is_antimeridian_safe() -> None:
    bbox = [170.0, -5.0, -170.0, 5.0]
    # -170.1 is 0.1 degrees (about 11 km) west of the -170 edge, inside the box.
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=0.0, lon=-170.1, cog=90.0), bbox)
    assert not left_coverage(Ping(mmsi=MMSI, t=T0, lat=0.0, lon=-170.1, cog=270.0), bbox)


@given(
    lat=st.floats(1.0, 4.0, allow_nan=False, allow_infinity=False),
    lon=st.floats(1.0, 4.0, allow_nan=False, allow_infinity=False),
    course=st.one_of(st.none(), st.floats(0.0, 360.0, allow_nan=False, allow_infinity=False)),
)
@SETTINGS
def test_left_coverage_is_false_well_inside_the_box(lat: float, lon: float, course: float | None) -> None:
    # One degree of margin is at least 110 km, comfortably more than the 25 km edge band.
    ping = Ping(mmsi=MMSI, t=T0, lat=lat, lon=lon, sog=10.0, cog=course)

    assert not left_coverage(ping, [0.0, 0.0, 5.0, 5.0])


@given(along=st.floats(1.0, 4.0, allow_nan=False, allow_infinity=False))
@SETTINGS
def test_left_coverage_is_true_on_every_edge_heading_straight_out(along: float) -> None:
    bbox = [0.0, 0.0, 5.0, 5.0]

    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=along, lon=0.0, cog=270.0), bbox)
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=along, lon=5.0, cog=90.0), bbox)
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=0.0, lon=along, cog=180.0), bbox)
    assert left_coverage(Ping(mmsi=MMSI, t=T0, lat=5.0, lon=along, cog=0.0), bbox)


# --- effective_cap_kn ---------------------------------------------------------------------


@given(
    class_cap=st.floats(0.0, 44.0, allow_nan=False, allow_infinity=False),
    sogs=st.lists(
        st.one_of(st.none(), st.floats(0.0, 102.3, allow_nan=False, allow_infinity=False)),
        max_size=8,
    ),
)
@SETTINGS
def test_effective_cap_never_drops_below_class_and_never_exceeds_ceiling(
    class_cap: float, sogs: list[float | None]
) -> None:
    track = [
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=i), lat=0.0, lon=0.0, sog=sog) for i, sog in enumerate(sogs)
    ]

    cap = effective_cap_kn(class_cap, track)

    assert class_cap <= cap <= 45.0


def test_effective_cap_raises_a_slow_declared_class_to_sustained_speed() -> None:
    track = [
        Ping(mmsi=MMSI, t=T0, lat=0.0, lon=0.0, sog=22.0),
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=10), lat=0.0, lon=0.0, sog=21.5),
    ]

    # 1.2 x the slower of the two fast reports, above a 15 kn cargo cap.
    assert effective_cap_kn(15.0, track) == pytest.approx(1.2 * 21.5)


def test_effective_cap_ignores_a_single_ping_spike_and_unavailable_sog() -> None:
    spike = [
        Ping(mmsi=MMSI, t=T0, lat=0.0, lon=0.0, sog=12.0),
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=10), lat=0.0, lon=0.0, sog=40.0),
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=20), lat=0.0, lon=0.0, sog=12.0),
    ]
    unavailable = [
        Ping(mmsi=MMSI, t=T0, lat=0.0, lon=0.0, sog=12.0),
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=10), lat=0.0, lon=0.0, sog=102.3),
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=20), lat=0.0, lon=0.0, sog=12.0),
    ]

    assert effective_cap_kn(15.0, spike) == pytest.approx(15.0)
    assert effective_cap_kn(15.0, unavailable) == pytest.approx(15.0)


def test_effective_cap_never_exceeds_the_ceiling_or_the_class_cap() -> None:
    fast = [
        Ping(mmsi=MMSI, t=T0, lat=0.0, lon=0.0, sog=40.0),
        Ping(mmsi=MMSI, t=T0 + timedelta(minutes=10), lat=0.0, lon=0.0, sog=40.0),
    ]

    assert effective_cap_kn(20.0, fast) == pytest.approx(45.0)
    assert effective_cap_kn(30.0, []) == pytest.approx(30.0)
    assert effective_cap_kn(30.0, fast[:1]) == pytest.approx(30.0)


# --- valid_mmsi ---------------------------------------------------------------------------


@pytest.mark.parametrize("mmsi", [201000001, 211234567, 249000001, 775999999])
def test_valid_mmsi_accepts_real_nine_digit_identities(mmsi: int) -> None:
    assert valid_mmsi(mmsi)


@pytest.mark.parametrize(
    "mmsi",
    [
        0,
        -211000001,
        21100000,
        2110000010,
        111111111,
        123456789,
        987654321,
        999999999,
        200000001,
        776000001,
    ],
)
def test_valid_mmsi_rejects_placeholders_and_bad_prefixes(mmsi: int) -> None:
    assert not valid_mmsi(mmsi)


@given(digit=st.integers(0, 9))
@SETTINGS
def test_valid_mmsi_rejects_every_repeated_digit_number(digit: int) -> None:
    assert not valid_mmsi(int(str(digit) * 9))


# --- open_silences: coverage exits and invalid identities ---------------------------------


def test_ship_leaving_the_baltic_box_westward_is_not_dark_but_eastward_is() -> None:
    baltic = next(area["bbox"] for area in LISTENING if area["id"] == "BALTIC_WATCH")
    assert baltic == [9.5, 53.5, 30.5, 66.0]
    now = T0 + timedelta(minutes=134)
    coverage = _coverage(T0, now)
    # 0.2 degrees east of the west edge: about 13 km, inside the 25 km edge band.
    westbound = [_ping(minutes=0, lat=55.0, lon=9.7, sog=11.0, cog=270.0)]
    eastbound = [_ping(minutes=0, lat=55.0, lon=9.7, sog=11.0, cog=90.0)]

    leaving_stats: dict[str, int] = {}
    leaving = open_silences({MMSI: westbound}, coverage, now, stats=leaving_stats)
    staying_stats: dict[str, int] = {}
    staying = open_silences({MMSI: eastbound}, coverage, now, stats=staying_stats)

    assert leaving == []
    assert leaving_stats["left_coverage"] == 1
    assert leaving_stats.get("emitted", 0) == 0
    assert [event.mmsi for event in staying] == [MMSI]
    assert staying_stats["emitted"] == 1
    assert "left_coverage" not in staying_stats
    # With no listening boxes supplied the edge rule cannot fire; the gap is reported.
    assert len(open_silences({MMSI: westbound}, coverage, now, areas=[])) == 1


def test_open_silences_skips_invalid_identities_and_counts_the_reason() -> None:
    now = T0 + timedelta(minutes=134)
    stats: dict[str, int] = {}

    events = open_silences({111111111: [_ping(minutes=0, sog=11.0)]}, _coverage(T0, now), now, stats=stats)

    assert events == []
    assert stats["invalid_mmsi"] == 1
    assert stats["considered"] == 1


# --- possible_contacts --------------------------------------------------------------------


@given(
    sog=st.floats(0.0, 3.0, allow_nan=False, allow_infinity=False),
    cap=st.floats(1.0, 25.0, allow_nan=False, allow_infinity=False),
    hours=st.floats(1.0, 24.0, allow_nan=False, allow_infinity=False),
)
@SETTINGS
def test_stopped_contact_on_the_last_fix_could_meet(sog: float, cap: float, hours: float) -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    tracks = {7: [Ping(mmsi=7, t=T0, lat=0.0, lon=0.0, sog=sog)]}

    contacts = possible_contacts(1, last, None, cap, T0, T0 + timedelta(hours=hours), tracks)

    assert len(contacts) == 1
    contact = contacts[0]
    assert isinstance(contact, Contact)
    assert contact.mmsi == 7
    assert contact.min_dist_km == pytest.approx(0.0)
    assert contact.stopped
    assert contact.could_meet


@given(
    cap=st.floats(1.0, 25.0, allow_nan=False, allow_infinity=False),
    hours=st.floats(1.0, 24.0, allow_nan=False, allow_infinity=False),
)
@SETTINGS
def test_a_ship_beyond_the_reach_radius_is_never_a_contact(cap: float, hours: float) -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    t_end = T0 + timedelta(hours=hours)
    radius = reach_km(cap, T0, t_end)
    lat, lon = _point(90.0, radius + 5.0)
    tracks = {2: [Ping(mmsi=2, t=t_end, lat=lat, lon=lon, sog=0.0)]}

    assert possible_contacts(1, last, None, cap, T0, t_end, tracks) == []


def test_contact_keeps_the_ping_closest_to_the_last_fix() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    near_lat, near_lon = _point(90.0, 10.0)
    far_lat, far_lon = _point(90.0, 40.0)
    tracks = {
        2: [
            Ping(mmsi=2, t=T0 + timedelta(hours=1), lat=far_lat, lon=far_lon, sog=8.0),
            Ping(mmsi=2, t=T0 + timedelta(hours=2), lat=near_lat, lon=near_lon, sog=8.0),
        ]
    }

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks)

    assert len(contacts) == 1
    assert contacts[0].lat == pytest.approx(near_lat)
    assert contacts[0].min_dist_km == pytest.approx(10.0, abs=0.1)
    assert not contacts[0].stopped
    assert not contacts[0].could_meet


def test_dark_ship_is_never_its_own_contact() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    tracks = {1: [Ping(mmsi=1, t=T0 + timedelta(hours=1), lat=0.0, lon=0.0, sog=0.0)]}

    assert possible_contacts(1, last, None, 20.0, T0, T0 + timedelta(hours=2), tracks) == []


def test_contacts_are_sorted_with_potential_meetings_first() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    moving_lat, moving_lon = _point(90.0, 40.0)
    stopped_lat, stopped_lon = _point(90.0, 100.0)
    tracks = {
        # At two hours the 30 kn cap has opened the reach to 111 km, so both are inside.
        2: [Ping(mmsi=2, t=T0 + timedelta(hours=2), lat=moving_lat, lon=moving_lon, sog=12.0)],
        3: [Ping(mmsi=3, t=T0 + timedelta(hours=2), lat=stopped_lat, lon=stopped_lon, sog=2.5)],
    }

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks)

    assert [c.mmsi for c in contacts] == [3, 2]
    assert contacts[0].could_meet and contacts[0].stopped
    assert not contacts[1].could_meet


def test_max_contacts_truncates_after_sorting() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    tracks = {
        mmsi: [
            Ping(
                mmsi=mmsi,
                t=T0 + timedelta(hours=1),
                lat=_point(90.0, km)[0],
                lon=_point(90.0, km)[1],
                sog=10.0,
            )
        ]
        for mmsi, km in ((2, 10.0), (3, 20.0), (4, 30.0))
    }

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks, max_contacts=2)

    assert [c.mmsi for c in contacts] == [2, 3]
    assert possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks, max_contacts=0) == []


def test_offshore_km_measures_the_gap_to_land_and_is_none_without_land() -> None:
    land = box(0.0, 0.0, 0.2, 0.2)

    assert offshore_km(0.4, 0.1, None) is None
    assert offshore_km(0.4, 0.1, land) == pytest.approx(0.2 * 111.0, rel=0.01)


def test_a_moving_ping_can_never_meet() -> None:
    assert not contact_could_meet(Ping(mmsi=2, t=T0, lat=0.0, lon=0.0, sog=9.0), None)


def test_contact_inside_a_port_anchorage_is_listed_but_cannot_meet() -> None:
    # Singapore Eastern anchorage: the hull is in reach and stopped, but waiting there is
    # ordinary bunkering, not a rendezvous.
    last = Fix(lat=1.30, lon=104.00, t=T0)
    tracks = {2: [Ping(mmsi=2, t=T0 + timedelta(hours=1), lat=1.30, lon=104.00, sog=0.0)]}

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks)

    assert len(contacts) == 1
    assert contacts[0].stopped
    assert not contacts[0].could_meet


def test_contact_inshore_is_listed_but_cannot_meet() -> None:
    land = box(0.0, 0.0, 0.1, 0.1)
    last = Fix(lat=0.0, lon=0.0, t=T0)
    tracks = {
        # Inside the land box: zero kilometres offshore.
        2: [Ping(mmsi=2, t=T0 + timedelta(hours=1), lat=0.05, lon=0.05, sog=0.0)],
        # Thirty kilometres from land, well inside a 30 kn reach over one hour.
        3: [Ping(mmsi=3, t=T0 + timedelta(hours=1), lat=0.0, lon=0.30, sog=0.0)],
    }

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks, land=land)
    by_mmsi = {contact.mmsi: contact for contact in contacts}

    assert by_mmsi[2].stopped and not by_mmsi[2].could_meet
    assert by_mmsi[3].stopped and by_mmsi[3].could_meet


def test_could_meet_uses_the_representative_ping_not_any_stopped_ping() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    moving_lat, moving_lon = _point(90.0, 10.0)
    stopped_lat, stopped_lon = _point(90.0, 40.0)
    tracks = {
        2: [
            Ping(mmsi=2, t=T0 + timedelta(hours=1), lat=moving_lat, lon=moving_lon, sog=9.0),
            Ping(mmsi=2, t=T0 + timedelta(hours=2), lat=stopped_lat, lon=stopped_lon, sog=0.0),
        ]
    }

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks)

    # The map shows the closest (moving) fix; a stop elsewhere in the disk is not a meeting.
    assert contacts[0].min_dist_km == pytest.approx(10.0, abs=0.1)
    assert not contacts[0].stopped
    assert not contacts[0].could_meet


def test_contact_stopped_at_its_representative_ping_could_meet() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    stopped_lat, stopped_lon = _point(90.0, 5.0)
    moving_lat, moving_lon = _point(90.0, 15.0)
    tracks = {
        2: [
            Ping(mmsi=2, t=T0 + timedelta(minutes=30), lat=stopped_lat, lon=stopped_lon, sog=0.0),
            Ping(mmsi=2, t=T0 + timedelta(hours=2), lat=moving_lat, lon=moving_lon, sog=9.0),
        ]
    }

    contacts = possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks)

    assert contacts[0].stopped
    assert contacts[0].could_meet
    # The map still shows the closest fix, which is the stopped one here.
    assert contacts[0].min_dist_km == pytest.approx(5.0, abs=0.1)


def test_pings_outside_the_gap_are_ignored() -> None:
    last = Fix(lat=0.0, lon=0.0, t=T0)
    tracks = {
        2: [
            Ping(mmsi=2, t=T0 - timedelta(hours=1), lat=0.0, lon=0.0, sog=0.0),
            Ping(mmsi=2, t=T0 + timedelta(hours=5), lat=0.0, lon=0.0, sog=0.0),
        ]
    }

    assert possible_contacts(1, last, None, 30.0, T0, T0 + timedelta(hours=3), tracks) == []
