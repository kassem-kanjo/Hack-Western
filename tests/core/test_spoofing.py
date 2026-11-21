"""Tests for the GNSS spoofing / ghost-point detector (issue #100 follow-up).

A ghost point is a place several unrelated ships *teleported to and back from*: their tracks are
normal before and after, but a fix tens of kilometres away implies an impossible speed in and an
impossible speed out. Ports and anchorages cannot look like that, because moored ships sit still
instead of jumping.

The real fixture is the recorded St Petersburg case: six unrelated ships sail normal tracks near
the Neva approaches, and five of them appear at a patch of land about 25 km north of the city at
different times in one afternoon.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from shapely.geometry import box

from darkfleet.core.kinematics import split_tracks
from darkfleet.core.spoofing import filter_explained_jumps, ghost_points
from darkfleet.core.tracks import Event, Ping
from darkfleet.ingest.ais_tracks import parse_line

T0 = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
LAT, LON = 59.0, 21.0  # open water in the Baltic, far from the fixture and from the test land
FIXTURES = Path(__file__).parents[1] / "fixtures" / "ais"


def _fixture_pings(name: str) -> list[Ping]:
    """Every position report in an aisstream fixture, ignoring static messages."""
    pings: list[Ping] = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        parsed = parse_line(line)
        if isinstance(parsed, Ping):
            pings.append(parsed)
    return pings


def _teleport(mmsi: int, lat: float = LAT, lon: float = LON, *, minutes: int = 5) -> list[Ping]:
    """A vessel on a real track ~50 km south that appears at (lat, lon) and then returns.

    The two false fixes are one minute apart; the jump in and the jump out are each about 50 km in
    five minutes, roughly 320 knots, which no hull class can do.
    """
    origin_lat = lat + 0.45
    return [
        Ping(mmsi=mmsi, t=T0, lat=origin_lat, lon=lon, sog=8.0),
        Ping(mmsi=mmsi, t=T0 + timedelta(minutes=minutes), lat=lat, lon=lon, sog=8.0),
        Ping(mmsi=mmsi, t=T0 + timedelta(minutes=minutes + 1), lat=lat, lon=lon, sog=8.0),
        Ping(mmsi=mmsi, t=T0 + timedelta(minutes=minutes + 6), lat=origin_lat, lon=lon, sog=8.0),
    ]


def _moored(mmsi: int, lat: float, lon: float, *, minutes: int = 30) -> list[Ping]:
    """A stationary hull at a berth: a continuous track at one point, speed over ground zero."""
    return [
        Ping(mmsi=mmsi, t=T0 + timedelta(minutes=minute), lat=lat, lon=lon, sog=0.0)
        for minute in range(0, minutes, 5)
    ]


def test_real_fixture_has_exactly_one_ghost_point() -> None:
    tracks = split_tracks(_fixture_pings("spoof_stpetersburg.jsonl"))

    ghosts = ghost_points(tracks)

    assert len(ghosts) == 1
    ghost = ghosts[0]
    assert ghost.kind == "spoof"
    assert ghost.other_mmsi is None
    # Strict count: only ships that themselves teleported >= 20 km in AND out (3 in this 5 h sample);
    # the earlier ">= 5" counted ordinary vessels that merely passed near the point.
    assert ghost.evidence["n_vessels"] >= 3
    assert abs(ghost.lat - 60.309) < 0.01
    assert abs(ghost.lon - 30.489) < 0.01
    assert ghost.start < ghost.end
    assert len(ghost.evidence["vessels"]) == ghost.evidence["n_vessels"]
    assert 0.0 < ghost.severity <= 1.0
    assert "jumped to the same point and back" in ghost.explanation
    assert "km from their real tracks" in ghost.explanation
    # Without a coastline the detector cannot know the point is inland; the out-and-back rule is
    # what found it.
    assert ghost.evidence["on_land"] is False
    assert ghost.evidence["inland_km"] == 0.0

    for vessel in ghost.evidence["vessels"]:
        assert set(vessel) == {
            "mmsi",
            "n_reports",
            "first",
            "last",
            "jump_in_kn",
            "jump_out_kn",
            "offset_km",
        }
        assert vessel["offset_km"] > 5.0


def test_a_moored_port_is_not_a_ghost_point() -> None:
    # Ten hulls at the same quay with speed over ground zero and continuous tracks: exactly the
    # shared-position pattern that produced 784 false events on five hours of Baltic data.
    tracks = {mmsi: _moored(mmsi, LAT, LON) for mmsi in range(111, 121)}

    assert ghost_points(tracks) == []


def test_three_vessels_teleporting_out_and_back_are_one_ghost_point() -> None:
    tracks = {mmsi: _teleport(mmsi) for mmsi in (111, 222, 333)}

    ghosts = ghost_points(tracks)

    assert len(ghosts) == 1
    ghost = ghosts[0]
    assert ghost.evidence["n_vessels"] == 3
    assert abs(ghost.lat - LAT) < 0.001
    assert abs(ghost.lon - LON) < 0.001
    for vessel in ghost.evidence["vessels"]:
        assert vessel["jump_in_kn"] > 300.0
        assert vessel["jump_out_kn"] > 300.0
        assert vessel["offset_km"] > 5.0


def test_a_synthetic_land_square_marks_the_point_inland() -> None:
    # Three ships teleport to a point > 1 km inside a square; land is reported as extra evidence
    # on an event the out-and-back rule already found.
    tracks = {mmsi: _teleport(mmsi, LAT, LON) for mmsi in (111, 222, 333)}
    land = box(LON - 0.1, LAT - 0.1, LON + 0.1, LAT + 0.1)

    ghosts = ghost_points(tracks, land=land)

    assert len(ghosts) == 1
    ghost = ghosts[0]
    assert ghost.evidence["on_land"] is True
    assert ghost.evidence["inland_km"] > 1.0
    assert ghost.evidence["n_vessels"] == 3
    assert ghost.severity == 0.7  # 3 / 6 sharing, plus 0.2 for land


def test_inland_positions_alone_are_not_a_ghost_point() -> None:
    # Four hulls moored well inside land: impossible, but a river berth looks the same and the
    # land signal must never be the trigger.
    tracks = {mmsi: _moored(mmsi, LAT, LON) for mmsi in (111, 222, 333, 444)}
    land = box(LON - 0.1, LAT - 0.1, LON + 0.1, LAT + 0.1)

    assert ghost_points(tracks, land=land) == []


def test_two_vessels_only_are_not_a_ghost_point() -> None:
    tracks = {111: _teleport(111), 222: _teleport(222)}

    assert ghost_points(tracks) == []


def test_a_shared_point_across_a_cell_boundary_is_found() -> None:
    # The three false fixes fall in adjacent 0.005-degree cells but within a few tens of metres of
    # each other; only the neighbourhood lookup connects them into one candidate.
    tracks = {
        111: _teleport(111, 59.0002, 21.0002),
        222: _teleport(222, 58.9998, 20.9998),
        333: _teleport(333, 59.0000, 21.0000),
    }

    ghosts = ghost_points(tracks)

    assert len(ghosts) == 1
    assert ghosts[0].evidence["n_vessels"] == 3


def test_ordinary_traffic_through_a_shared_point_is_not_a_ghost() -> None:
    # Every ship passes through the shared point continuously, ~1.1 km a minute. That is a lane,
    # not a falsified position: no leg is long enough to be a teleport.
    tracks: dict[int, list[Ping]] = {}
    for mmsi in (111, 222, 333):
        tracks[mmsi] = [
            Ping(mmsi=mmsi, t=T0, lat=LAT + 0.01, lon=LON + 0.01, sog=8.0),
            Ping(mmsi=mmsi, t=T0 + timedelta(minutes=1), lat=LAT, lon=LON, sog=8.0),
            Ping(mmsi=mmsi, t=T0 + timedelta(minutes=2), lat=LAT + 0.01, lon=LON + 0.01, sog=8.0),
        ]

    assert ghost_points(tracks) == []


def _jump(lat: float, lon: float, evidence: dict[str, object] | None = None) -> Event:
    return Event(
        kind="jump",
        mmsi=111,
        start=T0,
        end=T0 + timedelta(minutes=1),
        lat=lat,
        lon=lon,
        severity=1.0,
        explanation="test jump",
        evidence=evidence or {},
    )


def test_filter_explained_jumps_drops_only_jumps_at_a_ghost() -> None:
    tracks = {mmsi: _teleport(mmsi) for mmsi in (111, 222, 333)}
    ghosts = ghost_points(tracks)
    ghost = ghosts[0]

    at_ghost = _jump(ghost.lat + 0.001, ghost.lon)  # ~110 m away
    far_away = _jump(ghost.lat + 0.1, ghost.lon)  # ~11 km away
    evidence_at_ghost = _jump(ghost.lat + 0.5, ghost.lon, {"last_lat": ghost.lat, "last_lon": ghost.lon})

    kept = filter_explained_jumps([at_ghost, far_away, evidence_at_ghost], ghosts)

    assert kept == [far_away]


def test_filter_explained_jumps_is_a_noop_without_ghosts() -> None:
    jumps = [_jump(LAT, LON), _jump(LAT + 1.0, LON + 1.0)]

    assert filter_explained_jumps(jumps, []) == jumps
