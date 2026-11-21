"""Tests for the GNSS spoofing / ghost-point detector (issue #100).

The real fixture is the recorded St Petersburg case: six unrelated ships that sail normal tracks
near the Neva approaches all report a patch of land about 25 km north of the city at different
times in one afternoon. The synthetic tests pin the two rules and the false-positive guard that
the fixture needs: sharing alone must not turn an ordinary shipping lane into a spoofing event.
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


def _cluster(mmsi: int, lat: float, lon: float, *, minutes: int = 2) -> list[Ping]:
    """A few fixes in one place, enough for the sharing rule but not a moving track."""
    return [
        Ping(mmsi=mmsi, t=T0 + timedelta(minutes=minute), lat=lat, lon=lon, sog=7.5)
        for minute in range(minutes)
    ]


def test_real_fixture_has_exactly_one_ghost_point() -> None:
    tracks = split_tracks(_fixture_pings("spoof_stpetersburg.jsonl"))

    ghosts = ghost_points(tracks)

    assert len(ghosts) == 1
    ghost = ghosts[0]
    assert ghost.kind == "spoof"
    assert ghost.other_mmsi is None
    assert ghost.evidence["n_vessels"] >= 5
    assert abs(ghost.lat - 60.309) < 0.01
    assert abs(ghost.lon - 30.489) < 0.01
    assert ghost.start < ghost.end
    assert len(ghost.evidence["vessels"]) == ghost.evidence["n_vessels"]
    assert 0.0 < ghost.severity <= 1.0
    # Without a coastline the detector cannot know the point is inland, but it can see that the
    # reporting ships are nowhere near it on their own tracks.
    assert ghost.evidence["on_land"] is False
    assert ghost.evidence["inland_km"] == 0.0


def test_a_synthetic_land_square_marks_the_point_inland() -> None:
    # Three ships at one point inside a square large enough that the point is > 1 km from every
    # edge; the negative buffer therefore keeps the whole cluster.
    tracks = {mmsi: _cluster(mmsi, LAT, LON) for mmsi in (111, 222, 333)}
    land = box(LON - 0.1, LAT - 0.1, LON + 0.1, LAT + 0.1)

    ghosts = ghost_points(tracks, land=land)

    assert len(ghosts) == 1
    ghost = ghosts[0]
    assert ghost.evidence["on_land"] is True
    assert ghost.evidence["inland_km"] > 1.0
    assert ghost.evidence["n_vessels"] == 3
    assert ghost.severity == 0.7  # 3 / 6 sharing, plus 0.2 for land


def test_one_ship_inland_is_still_a_ghost_point() -> None:
    # Criterion (b) stands alone: a single hull that puts itself 10 km inside land cannot be there.
    tracks = {111: _cluster(111, LAT, LON)}
    land = box(LON - 0.1, LAT - 0.1, LON + 0.1, LAT + 0.1)

    ghosts = ghost_points(tracks, land=land)

    assert len(ghosts) == 1
    assert ghosts[0].evidence["n_vessels"] == 1
    assert ghosts[0].evidence["on_land"] is True


def test_two_vessels_only_are_not_a_ghost_point() -> None:
    tracks = {111: _cluster(111, LAT, LON), 222: _cluster(222, LAT, LON)}

    assert ghost_points(tracks) == []


def test_a_shared_point_across_a_cell_boundary_is_found() -> None:
    # The three fixes fall in adjacent 0.005-degree cells but within a few tens of metres of each
    # other; only the neighbourhood lookup connects them into one candidate.
    tracks = {
        111: _cluster(111, 59.0002, 21.0002),
        222: _cluster(222, 58.9998, 20.9998),
        333: _cluster(333, 59.0000, 21.0000),
    }

    ghosts = ghost_points(tracks)

    assert len(ghosts) == 1
    assert ghosts[0].evidence["n_vessels"] == 3


def test_ordinary_traffic_through_a_shared_point_is_not_a_ghost() -> None:
    # Every ship reports the shared point once, but also reports ~1.1 km away a minute before and
    # after. That is a passage through a lane, not a falsified position: all three vessels are
    # ordinary traffic, so the sharing rule alone must not fire.
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
    tracks = {mmsi: _cluster(mmsi, LAT, LON) for mmsi in (111, 222, 333)}
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
