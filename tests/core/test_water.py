"""Tests for clipping prism zones to water (issue #59)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon, mapping

from darkfleet.core.water import clip_to_water, load_land

LAND_SQUARE = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0)])
WATER_SQUARE = Polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])


def _write_land(tmp_path: Path, geometries: list[Polygon]) -> Path:
    """A minimal GeoJSON FeatureCollection so load_land can be exercised end to end."""
    path = tmp_path / "land.geojson"
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": mapping(geometry)} for geometry in geometries
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_square_half_over_land_is_halved(tmp_path: Path) -> None:
    land = load_land(_write_land(tmp_path, [LAND_SQUARE]))

    water = clip_to_water(WATER_SQUARE, land)

    assert water.area == pytest.approx(WATER_SQUARE.area / 2.0)
    assert water.bounds == pytest.approx((1.0, 0.0, 2.0, 2.0))
    assert water.geom_type in {"Polygon", "MultiPolygon"}


def test_load_land_unions_features_and_caches(tmp_path: Path) -> None:
    other = Polygon([(3.0, 0.0), (4.0, 0.0), (4.0, 1.0), (3.0, 1.0)])
    path = _write_land(tmp_path, [LAND_SQUARE, other])

    land = load_land(path)

    assert land.geom_type == "MultiPolygon"
    assert land.area == pytest.approx(LAND_SQUARE.area + other.area)
    assert load_land(path) is land  # lru_cache keyed by path


def test_empty_input_stays_empty() -> None:
    assert clip_to_water(Polygon(), LAND_SQUARE).is_empty
    assert clip_to_water(MultiPolygon(), LAND_SQUARE).is_empty


def test_invalid_input_does_not_raise() -> None:
    bowtie = Polygon([(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)])

    water = clip_to_water(bowtie, LAND_SQUARE)

    assert water.geom_type in {"Polygon", "MultiPolygon"}
    assert water.is_valid
