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


HORMUZ = polygon_from_bbox(54.0, 60.5, 22.0, 27.5)
BALTIC_WATCH = polygon_from_bbox(9.5, 30.5, 53.5, 66.0)
# Open Gulf of Oman water outside the strait, covered by the same Sentinel-1 swaths. It
# serves as a control for the Hormuz radar series: nearby water, same sensor, no strait.
GULF_OF_OMAN_CONTROL = polygon_from_bbox(57.6, 59.8, 23.6, 25.2)

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
