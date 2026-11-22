"""Properties of the outage-cluster rule and the reach radius (#219)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.hormuz_stories import (
    Gap,
    cluster_gaps,
    cluster_summary,
    reach_radius_km,
)

T0 = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)


def gap(i: int, *, minutes: int = 0, key: str | None = None, lat: float = 25.2, lon: float = 54.6) -> Gap:
    start = T0 + timedelta(minutes=minutes)
    return Gap(
        id=f"g{i}",
        vessel_key=key or f"v{i}",
        start=start,
        end=start + timedelta(hours=2),
        off_lat=lat,
        off_lon=lon,
        on_lat=lat,
        on_lon=lon,
    )


def ids(clusters: list[tuple[Gap, ...]]) -> set[frozenset[str]]:
    return {frozenset(g.id for g in members) for members in clusters}


# Positions: a few sites a few hundred km apart, plus jitter inside one site.
gap_strategy = st.builds(
    lambda i, minutes, key, site, jitter_lat, jitter_lon: Gap(
        id=f"g{i}",
        vessel_key=f"v{key}",
        start=T0 + timedelta(minutes=minutes),
        end=T0 + timedelta(minutes=minutes + 120),
        off_lat=[25.2, 25.9, 24.1][site] + jitter_lat,
        off_lon=[54.6, 56.9, 58.4][site] + jitter_lon,
        on_lat=25.0,
        on_lon=55.0,
    ),
    i=st.integers(0, 10_000),
    minutes=st.integers(0, 48 * 60),
    key=st.integers(0, 15),
    site=st.integers(0, 2),
    jitter_lat=st.floats(-0.2, 0.2),
    jitter_lon=st.floats(-0.2, 0.2),
)


@settings(max_examples=150, deadline=None)
@given(st.lists(gap_strategy, max_size=40, unique_by=lambda g: g.id))
def test_permutation_invariant(gaps: list[Gap]) -> None:
    forward = cluster_gaps(gaps)
    backward = cluster_gaps(list(reversed(gaps)))
    assert ids(forward) == ids(backward)


@settings(max_examples=150, deadline=None)
@given(st.lists(gap_strategy, max_size=40, unique_by=lambda g: g.id))
def test_every_cluster_has_enough_distinct_ships(gaps: list[Gap]) -> None:
    for members in cluster_gaps(gaps):
        assert len({g.vessel_key for g in members}) >= 8


@settings(max_examples=150, deadline=None)
@given(st.lists(gap_strategy, max_size=40, unique_by=lambda g: g.id))
def test_clusters_are_disjoint(gaps: list[Gap]) -> None:
    seen: set[str] = set()
    for members in cluster_gaps(gaps):
        names = {g.id for g in members}
        assert not names & seen
        seen |= names


@settings(max_examples=100, deadline=None)
@given(st.lists(gap_strategy, max_size=40, unique_by=lambda g: g.id), st.integers(2, 12))
def test_stricter_minimum_keeps_a_subset(gaps: list[Gap], minimum: int) -> None:
    loose = ids(cluster_gaps(gaps, min_vessels=minimum))
    strict = ids(cluster_gaps(gaps, min_vessels=minimum + 1))
    assert strict <= loose


@settings(max_examples=100, deadline=None)
@given(st.lists(gap_strategy, max_size=40, unique_by=lambda g: g.id))
def test_one_ship_is_never_a_cluster(gaps: list[Gap]) -> None:
    same_ship = [replace(g, vessel_key="only") for g in gaps]
    assert cluster_gaps(same_ship, min_vessels=2) == []


def test_eight_ships_within_the_rule_make_a_cluster() -> None:
    gaps = [gap(i, minutes=i * 10, lat=25.2 + i * 0.01) for i in range(8)]
    clusters = cluster_gaps(gaps)
    assert len(clusters) == 1
    summary = cluster_summary(clusters[0])
    assert (summary["vessels"], summary["gaps"], summary["span_hours"]) == (8, 8, 1.17)


def test_seven_ships_are_not_enough() -> None:
    gaps = [gap(i, minutes=i * 10) for i in range(7)]
    assert cluster_gaps(gaps) == []


def test_far_apart_gaps_are_never_linked() -> None:
    gaps = [gap(i, minutes=0, lat=25.2) for i in range(4)] + [
        gap(i + 10, minutes=0, lat=27.5) for i in range(4)
    ]
    # Each site is 4 ships; the two sites are ~260 km apart, so neither makes a cluster of 8.
    assert cluster_gaps(gaps) == []


def test_gaps_more_than_the_window_apart_do_not_link() -> None:
    gaps = [gap(i, minutes=i * 200) for i in range(10)]  # 3.33 h apart, every neighbour
    assert cluster_gaps(gaps) == []


def test_reach_radius_is_speed_times_gap_hours() -> None:
    g = gap(1)  # two hours long
    assert reach_radius_km(10.0, g) == 10.0 * 1.852 * 2.0
    assert reach_radius_km(0.0, g) == 0.0


def test_reappearance_anchor_groups_ships_that_came_back_together() -> None:
    # Ships go dark across ~500 km but all reappear at one spot within the window (#219, 12 Mar).
    gaps = [
        Gap(
            id=f"g{i}",
            vessel_key=f"v{i}",
            start=T0 + timedelta(minutes=i * 5),
            end=T0 + timedelta(minutes=i * 5 + 120),
            off_lat=19.5 + i * 0.2,
            off_lon=62.6 + i * 0.6,
            on_lat=25.22 + i * 0.001,
            on_lon=54.58,
        )
        for i in range(8)
    ]
    assert cluster_gaps(gaps, anchor="off") == []
    assert len(cluster_gaps(gaps, anchor="on")) == 1


def test_risk_tags_exclude_port_inspection_only_records() -> None:
    from darkfleet.core.hormuz_stories import is_risk_tagged

    assert is_risk_tagged(["mare.shadow", "poi"])
    assert is_risk_tagged(["sanction"])
    # The Tokyo MOU PSC rows carry no risk tag at all; a detention alone is a port outcome.
    assert not is_risk_tagged([])
    assert not is_risk_tagged(["mare.detained"])


def test_imo_mismatch_rejects_only_when_both_sides_have_one() -> None:
    from darkfleet.core.hormuz_stories import imo_agrees

    assert imo_agrees("9329758", "9329758")
    assert not imo_agrees("9329758", "9187370")
    assert imo_agrees(None, "9329758")
    assert imo_agrees("9329758", None)
    assert imo_agrees(None, None)
