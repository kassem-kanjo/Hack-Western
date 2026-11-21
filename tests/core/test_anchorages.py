"""Tests for the hand-drawn anchorage table (issue #118)."""

from __future__ import annotations

from darkfleet.core import anchorages
from darkfleet.core.anchorages import (
    ANCHORAGES,
    BOUNDARY_NOTE,
    STS_HOTSPOTS,
    context_for,
    sts_hotspot_for,
)

STS_NOTE = "known ship-to-ship transfer area for sanctioned oil (approximate box)"


def test_singapore_eastern_point_is_a_bunkering_anchorage() -> None:
    context = context_for(1.28, 103.95)

    assert context is not None
    assert context["name"] == "Singapore Eastern anchorage"
    assert context["kind"] == "bunkering"
    assert context["note"] == BOUNDARY_NOTE


def test_open_water_has_no_context() -> None:
    # Mid-Baltic, far from every box in the table.
    assert context_for(58.0, 19.0) is None


def test_box_edges_count_as_inside() -> None:
    # The south-west corner of the Singapore Eastern box: inclusive on every edge.
    context = context_for(1.24, 103.85)

    assert context is not None
    assert context["name"] == "Singapore Eastern anchorage"


def test_first_match_wins_when_boxes_overlap(monkeypatch) -> None:
    first = {
        "name": "first",
        "bbox": [-1.0, -1.0, 1.0, 1.0],
        "kind": "anchorage",
        "note": BOUNDARY_NOTE,
    }
    second = {
        "name": "second",
        "bbox": [0.0, 0.0, 2.0, 2.0],
        "kind": "bunkering",
        "note": BOUNDARY_NOTE,
    }
    monkeypatch.setattr(anchorages, "ANCHORAGES", [first, second])

    assert context_for(0.5, 0.5) == first


def test_every_box_is_well_formed() -> None:
    for anchorage in ANCHORAGES:
        min_lon, min_lat, max_lon, max_lat = anchorage["bbox"]
        assert min_lon < max_lon
        assert min_lat < max_lat
        assert anchorage["kind"] in {"anchorage", "bunkering"}
        assert anchorage["note"] == BOUNDARY_NOTE


def test_eopl_is_not_treated_as_a_routine_anchorage() -> None:
    # The EOPL box used to sit in ANCHORAGES, which discounted meetings there as bunkering.
    # It is a known sanctioned-oil transfer area, so context_for must not match it.
    assert context_for(1.35, 104.5) is None
    assert all(entry["name"] != "Johor/EOPL (Malaysia)" for entry in ANCHORAGES)
    assert all(entry["bbox"] != [104.30, 1.25, 104.75, 1.45] for entry in ANCHORAGES)


def test_eopl_is_a_ship_to_ship_hotspot() -> None:
    hotspot = sts_hotspot_for(1.35, 104.5)

    assert hotspot is not None
    assert hotspot["name"] == "EOPL"
    assert hotspot["bbox"] == [104.30, 1.25, 104.75, 1.45]
    assert hotspot["kind"] == "sts"
    assert hotspot["note"] == STS_NOTE


def test_sts_hotspot_edges_are_inside_and_open_water_is_not() -> None:
    assert sts_hotspot_for(1.25, 104.30) is not None
    assert sts_hotspot_for(58.0, 19.0) is None


def test_every_sts_hotspot_is_well_formed() -> None:
    for hotspot in STS_HOTSPOTS:
        min_lon, min_lat, max_lon, max_lat = hotspot["bbox"]
        assert min_lon < max_lon
        assert min_lat < max_lat
        assert hotspot["kind"] == "sts"
        assert hotspot["note"] == STS_NOTE
