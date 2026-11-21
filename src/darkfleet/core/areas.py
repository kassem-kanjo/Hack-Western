"""Named areas for the V3 watch screen (issue #86).

Two different jobs, kept apart:

* **Listening areas** are the boxes the recorder actually points its websocket at. They are
  wide - a feed subscription, not a statement of interest - so they tell the UI which slice of
  the world has data, and nothing more.
* **Watched areas** are the narrow places the watch screen draws attention to: straits, port
  approaches and known ship-to-ship hubs. A dark ship or a meeting inside one of these is
  surfaced; the same geometry in open water is not.

Every box is a conservative hand-drawn approximation, in `[min_lon, min_lat, max_lon,
max_lat]` order. Where a box comes from a public port or strait coordinate, the comment names
the place it is centred on; "approximate box" is the honest description, and the UI shows the
area name rather than claiming a legal boundary. Boxes never claim to be authoritative.

Pure: a fixed table and a point-in-box test, no files and no network.
"""

from __future__ import annotations

from typing import TypedDict

from .anchorages import STS_HOTSPOTS


class Area(TypedDict):
    """One named box, in the shape the watch contract's `areas` array expects."""

    id: str
    name: str
    kind: str  # "listening" | "watched"
    bbox: list[float]  # [min_lon, min_lat, max_lon, max_lat]


def _eopl_bbox() -> list[float]:
    """The EOPL ship-to-ship box, borrowed from the anchorage table rather than restated.

    The EOPL is already drawn once in :mod:`core.anchorages` for meeting context; reusing it
    keeps a watched area and a hot-spot verdict from ever disagreeing about the same water.
    """
    for hotspot in STS_HOTSPOTS:
        if hotspot["name"] == "EOPL":
            return list(hotspot["bbox"])
    raise KeyError("EOPL hot spot missing from core.anchorages.STS_HOTSPOTS")


LISTENING: list[Area] = [
    # Mirrors REGIONS in scripts/record_ais.py (the recorder's aisstream subscriptions). That
    # file is a script with argparse/dotenv wiring, not an importable package module, so the
    # boxes are restated here; tests/core/test_areas.py fails if they drift from
    # darkfleet.ingest.regions, the in-package copy of the same two regions.
    {
        # Baltic Sea and the Gulf of Finland: 53.5-66.0 N, 9.5-30.5 E.
        "id": "BALTIC_WATCH",
        "name": "Baltic",
        "kind": "listening",
        "bbox": [9.5, 53.5, 30.5, 66.0],
    },
    {
        # Persian Gulf and Gulf of Oman: 22.0-27.5 N, 54.0-60.5 E.
        "id": "HORMUZ",
        "name": "Hormuz approaches",
        "kind": "listening",
        "bbox": [54.0, 22.0, 60.5, 27.5],
    },
    {
        # Eastern Outer Port Limits off Johor/Singapore: 0.8-2.6 N, 103.3-105.8 E.
        "id": "MALAYSIA_EOPL",
        "name": "Malaysia EOPL",
        "kind": "listening",
        "bbox": [103.3, 0.8, 105.8, 2.6],
    },
    {
        # Laconian Gulf, southern Greece: 35.8-37.2 N, 21.8-23.6 E. The recorder calls this
        # region LACONIA; the listening entry is suffixed so its id cannot collide with the
        # narrow watched area of the same name, which area_of returns for that water.
        "id": "LACONIA_WATCH",
        "name": "Laconian Gulf approaches",
        "kind": "listening",
        "bbox": [21.8, 35.8, 23.6, 37.2],
    },
    {
        # Red Sea and Bab el-Mandeb: 11.5-20.5 N, 38.0-44.5 E.
        "id": "RED_SEA",
        "name": "Red Sea",
        "kind": "listening",
        "bbox": [38.0, 11.5, 44.5, 20.5],
    },
    {
        # Black Sea: 40.5-47.5 N, 27.0-42.0 E.
        "id": "BLACK_SEA",
        "name": "Black Sea",
        "kind": "listening",
        "bbox": [27.0, 40.5, 42.0, 47.5],
    },
]

WATCHED: list[Area] = [
    {
        # Strait of Hormuz and its approaches. The strait narrows between the Musandam
        # peninsula (about 26.4 N 56.5 E) and Qeshm island; the box runs from the Persian Gulf
        # entrance near 55.0 E to the Gulf of Oman near 57.6 E. Approximate.
        "id": "HORMUZ_STRAIT",
        "name": "Strait of Hormuz",
        "kind": "watched",
        "bbox": [55.0, 24.0, 57.6, 27.2],
    },
    {
        # Malaysia Eastern Outer Port Limits, the known sanctioned-oil ship-to-ship area off
        # Johor. Reuses the anchorage hot spot box (see _eopl_bbox) instead of redrawing it.
        "id": "EOPL_STS",
        "name": "EOPL ship-to-ship",
        "kind": "watched",
        "bbox": _eopl_bbox(),
    },
    {
        # Gulf of Finland oil ports and their approaches: Primorsk (about 60.36 N 28.61 E,
        # the Baltic Pipeline System terminal), Ust-Luga (about 59.66 N 28.30 E) and Vysotsk
        # (about 60.63 N 28.57 E). Box spans 27.6-29.6 E, 59.3-60.9 N. Approximate.
        "id": "GULF_OF_FINLAND_PORTS",
        "name": "Gulf of Finland ports",
        "kind": "watched",
        "bbox": [27.6, 59.3, 29.6, 60.9],
    },
    {
        # Kerch Strait between Crimea and the Taman peninsula, including the Kerch (about
        # 45.36 N 36.47 E) and Kavkaz (about 45.33 N 36.67 E) strait crossings. Approximate.
        "id": "KERCH",
        "name": "Kerch Strait",
        "kind": "watched",
        "bbox": [36.2, 44.8, 36.9, 45.5],
    },
    {
        # Laconian Gulf off southern Greece, a ship-to-ship transfer area for Russian oil
        # cargoes, centred near 36.5 N 22.8 E and kept inside the recorder's LACONIA box.
        "id": "LACONIA",
        "name": "Laconian Gulf STS",
        "kind": "watched",
        "bbox": [22.2, 36.1, 23.3, 36.9],
    },
]


def _contains(box: Area, lat: float, lon: float) -> bool:
    """True when `(lat, lon)` is inside `box`, edges included."""
    min_lon, min_lat, max_lon, max_lat = box["bbox"]
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def area_of(lat: float, lon: float) -> str | None:
    """The id of the watched area containing `(lat, lon)`, else a listening area, else None.

    Watched areas are tested first so a point inside both a wide subscription box and a narrow
    watched box is reported as the narrow one: the screen wants "Strait of Hormuz", not
    "Hormuz approaches". Within each list the first match wins, so the order of WATCHED is the
    priority order and overlapping entries resolve deterministically. Edges count as inside,
    matching :func:`core.anchorages.context_for`.
    """
    for box in WATCHED:
        if _contains(box, lat, lon):
            return box["id"]
    for box in LISTENING:
        if _contains(box, lat, lon):
            return box["id"]
    return None
