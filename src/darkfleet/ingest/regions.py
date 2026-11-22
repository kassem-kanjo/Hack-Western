"""Named operating regions as GeoJSON Polygons (lon/lat, RFC 7946 winding is not enforced)."""

from __future__ import annotations

import copy


def polygon_from_bbox(lon_min: float, lon_max: float, lat_min: float, lat_max: float) -> dict:
    """Return a closed GeoJSON Polygon ring for a bounding box."""
    ring = [
        [lon_min, lat_min],
        [lon_max, lat_min],
        [lon_max, lat_max],
        [lon_min, lat_max],
        [lon_min, lat_min],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def region_bbox(region: dict) -> tuple[float, float, float, float]:
    """Return ``(lon_min, lon_max, lat_min, lat_max)`` from a single-ring Polygon."""
    if not isinstance(region, dict):
        raise ValueError(f"expected a Polygon with one ring, got {type(region).__name__}")
    coordinates = region.get("coordinates")
    if region.get("type") != "Polygon" or not isinstance(coordinates, list) or len(coordinates) != 1:
        raise ValueError(f"expected a Polygon with one ring, got {type(region).__name__}")
    points = coordinates[0]
    if not isinstance(points, list) or not points:
        raise ValueError("expected a Polygon with one non-empty ring")
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lons), max(lons), min(lats), max(lats)


def overlap_area(a: dict, b: dict) -> float:
    """Area in square degrees of the bounding-box intersection of two regions.

    Exact for the rectangles this module builds; a non-rectangular polygon gets
    its bounding box.
    """
    a_lon_min, a_lon_max, a_lat_min, a_lat_max = region_bbox(a)
    b_lon_min, b_lon_max, b_lat_min, b_lat_max = region_bbox(b)
    lon_overlap = max(0.0, min(a_lon_max, b_lon_max) - max(a_lon_min, b_lon_min))
    lat_overlap = max(0.0, min(a_lat_max, b_lat_max) - max(a_lat_min, b_lat_min))
    return lon_overlap * lat_overlap


# The earlier Hormuz box ran to 60.5 E and contained the Gulf of Oman control
# (57.6-59.8 E), so the comparison was a region against a subset of itself. The
# treated area is now the strait and Persian Gulf side, west of the control at 57.6 E.
HORMUZ = polygon_from_bbox(54.0, 57.6, 22.0, 27.5)
BALTIC_WATCH = polygon_from_bbox(9.5, 30.5, 53.5, 66.0)
# Open Gulf of Oman water outside the strait, covered by the same Sentinel-1 swaths. It
# serves as a control for the Hormuz radar series: nearby water, same sensor, no strait.
GULF_OF_OMAN_CONTROL = polygon_from_bbox(57.6, 59.8, 23.6, 25.2)
# Note (#266): hormuz_history.json, hormuz_unseen.json and hormuz_stories.json were built on
# the old wide box (54.0-60.5 E, which includes the Gulf of Oman; SEA 1 went dark there).
# Re-running their builders now uses the narrower box above and drops Gulf of Oman stories.

REGIONS: dict[str, dict] = {
    "HORMUZ": HORMUZ,
    "BALTIC_WATCH": BALTIC_WATCH,
    "GULF_OF_OMAN_CONTROL": GULF_OF_OMAN_CONTROL,
}


def region_geometry(name: str) -> dict:
    """Return a deep copy of a named region so callers cannot mutate the registry."""
    try:
        region = REGIONS[name]
    except KeyError:
        raise KeyError(f"unknown region {name!r}; valid names: {', '.join(sorted(REGIONS))}") from None
    return copy.deepcopy(region)
