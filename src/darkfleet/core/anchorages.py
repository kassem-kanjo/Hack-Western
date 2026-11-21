"""Known anchorages and ship-to-ship hot spots, for reading a meeting's location as context.

A ship-to-ship transfer is not automatically suspicious. In Singapore, Fujairah or Gibraltar,
refuelling alongside another hull is ordinary port business, so a meeting there deserves a lower
score than the same geometry in open water. The list below is deliberately small: seven routine
boxes that cover the anchorages the Baltic and Malacca recordings actually visit.

The Eastern Outer Port Limits (EOPL) off Johor are a different signal. They are a known transfer
area for sanctioned oil, so they are kept in :data:`STS_HOTSPOTS` rather than in the routine
anchorage table: a meeting there is not discounted as bunkering, it is flagged for a closer look.

The boxes are drawn by hand around the published extents, not traced from any authority's
boundary. That is why every entry carries :data:`BOUNDARY_NOTE`: the answer means "this is the
well-known waiting area", not "this coordinate is legally inside an anchorage".

Pure: a fixed table and a point-in-box test, no files and no network.
"""

from __future__ import annotations

from typing import TypedDict


class Anchorage(TypedDict):
    """One approximate anchorage box and the kind of waiting that happens in it."""

    name: str
    bbox: list[float]
    kind: str
    note: str


BOUNDARY_NOTE = "approximate box drawn by hand, not an official boundary"
"""Shown verbatim wherever an anchorage match is used as evidence."""

ANCHORAGES: list[Anchorage] = [
    {
        "name": "Singapore Eastern anchorage",
        "bbox": [103.85, 1.24, 104.06, 1.33],
        "kind": "bunkering",
        "note": BOUNDARY_NOTE,
    },
    {
        "name": "Singapore Western anchorage",
        "bbox": [103.70, 1.18, 103.84, 1.28],
        "kind": "bunkering",
        "note": BOUNDARY_NOTE,
    },
    {
        "name": "Gothenburg approaches",
        "bbox": [11.50, 57.50, 11.85, 57.75],
        "kind": "anchorage",
        "note": BOUNDARY_NOTE,
    },
    {
        "name": "Skagen anchorage",
        "bbox": [10.40, 57.60, 10.90, 57.85],
        "kind": "anchorage",
        "note": BOUNDARY_NOTE,
    },
    {
        "name": "Fujairah anchorage",
        "bbox": [56.33, 25.00, 56.62, 25.35],
        "kind": "bunkering",
        "note": BOUNDARY_NOTE,
    },
    {
        "name": "Gibraltar bay",
        "bbox": [-5.45, 36.05, -5.30, 36.20],
        "kind": "bunkering",
        "note": BOUNDARY_NOTE,
    },
    {
        "name": "Kiel Bight anchorage",
        "bbox": [10.15, 54.40, 10.45, 54.55],
        "kind": "anchorage",
        "note": BOUNDARY_NOTE,
    },
]
"""Anchorage boxes in ``[min_lon, min_lat, max_lon, max_lat]`` order.

Order matters only where boxes touch or overlap: :func:`context_for` returns the first match,
so a point in two boxes is attributed to the one listed first (the more specific area).
"""

STS_HOTSPOTS: list[Anchorage] = [
    {
        "name": "EOPL",
        "bbox": [104.30, 1.25, 104.75, 1.45],
        "kind": "sts",
        "note": "known ship-to-ship transfer area for sanctioned oil (approximate box)",
    },
]
"""Boxes where transfers of sanctioned oil are known to happen, not routine bunkering.

Kept apart from :data:`ANCHORAGES` so a meeting here is never discounted as ordinary
refuelling. The Johor/EOPL box is approximate and hand-drawn; it is evidence of context,
not proof that a particular transfer was illicit.
"""


def _first_match(boxes: list[Anchorage], lat: float, lon: float) -> Anchorage | None:
    """The first box containing ``(lat, lon)``, with inclusive edges, or None."""
    for box in boxes:
        min_lon, min_lat, max_lon, max_lat = box["bbox"]
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            return box
    return None


def context_for(lat: float, lon: float) -> Anchorage | None:
    """The first routine anchorage box containing ``(lat, lon)``, or None for open water.

    Box edges count as inside: at this resolution a few hundred metres either way does not
    change whether a meeting was ordinary refuelling. The first match wins so overlapping
    boxes resolve deterministically, and an empty table answers None without a special case.
    Ship-to-ship hot spots are deliberately not returned here; use :func:`sts_hotspot_for`.
    """
    return _first_match(ANCHORAGES, lat, lon)


def sts_hotspot_for(lat: float, lon: float) -> Anchorage | None:
    """The first ship-to-ship hot spot containing ``(lat, lon)``, or None elsewhere."""
    return _first_match(STS_HOTSPOTS, lat, lon)
