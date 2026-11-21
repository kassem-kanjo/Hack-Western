"""Tests for the hand-drawn anchorage table (issue #118)."""

from __future__ import annotations

from darkfleet.core import anchorages
from darkfleet.core.anchorages import ANCHORAGES, BOUNDARY_NOTE, context_for


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
