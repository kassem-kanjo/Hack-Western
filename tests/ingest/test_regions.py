"""Offline tests for the regional GeoJSON helpers."""

import pytest

from darkfleet.ingest.regions import BALTIC_WATCH, HORMUZ, REGIONS, polygon_from_bbox, region_geometry


def _ring(region: dict) -> list:
    return region["coordinates"][0]


def test_polygon_ring_is_closed_and_ordered():
    region = polygon_from_bbox(1.0, 2.0, 3.0, 4.0)

    assert region["type"] == "Polygon"
    ring = _ring(region)
    assert ring == [
        [1.0, 3.0],
        [2.0, 3.0],
        [2.0, 4.0],
        [1.0, 4.0],
        [1.0, 3.0],
    ]
    assert len(ring) == 5
    assert ring[0] == ring[-1]
    assert all(isinstance(point, list) for point in ring)


def test_hormuz_bbox():
    ring = _ring(HORMUZ)
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]

    assert min(lons) == 54.0
    assert max(lons) == 60.5
    assert min(lats) == 22.0
    assert max(lats) == 27.5
    assert ring[0] == ring[-1]
    assert len(ring) == 5


def test_baltic_watch_bbox():
    ring = _ring(BALTIC_WATCH)
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]

    assert min(lons) == 9.5
    assert max(lons) == 30.5
    assert min(lats) == 53.5
    assert max(lats) == 66.0
    assert ring[0] == ring[-1]
    assert len(ring) == 5


def test_regions_keys():
    assert set(REGIONS) == {"HORMUZ", "BALTIC_WATCH"}


def test_region_geometry_returns_a_copy():
    geom = region_geometry("HORMUZ")
    geom["coordinates"][0][0][0] = 999.0

    assert REGIONS["HORMUZ"]["coordinates"][0][0][0] == 54.0
    assert region_geometry("HORMUZ")["coordinates"][0][0][0] == 54.0


def test_region_geometry_unknown_name():
    with pytest.raises(KeyError) as excinfo:
        region_geometry("NOWHERE")

    assert "HORMUZ" in str(excinfo.value)
    assert "BALTIC_WATCH" in str(excinfo.value)
