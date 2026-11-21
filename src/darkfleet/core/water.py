"""Clip prism zones to water using a coarse land polygon.

Everything here is pure geometry except :func:`load_land`, which reads a GeoJSON land
file (Natural Earth 50 m in this repo). Snapshot zones are clipped with a plain planar
`difference`: at 50 m resolution the coastline error dwarfs the projection error, so a
metric reprojection would buy nothing.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def _polygonal(geom: BaseGeometry | None) -> Polygon | MultiPolygon:
    """Keep only polygonal parts, preserving multi-part output (empty Polygon if none)."""
    if geom is None or geom.is_empty:
        return Polygon()
    if geom.geom_type == "Polygon" or geom.geom_type == "MultiPolygon":
        return geom
    parts: list[Polygon | MultiPolygon] = []
    for part in getattr(geom, "geoms", ()):
        if part.geom_type == "Polygon" or part.geom_type == "MultiPolygon":
            parts.append(part)
        elif part.geom_type == "GeometryCollection":
            sub = _polygonal(part)
            if not sub.is_empty:
                parts.append(sub)
    if not parts:
        return Polygon()
    if len(parts) == 1:
        return parts[0]
    union = unary_union(parts)
    return union if union.geom_type in ("Polygon", "MultiPolygon") else _polygonal(union)


def _repair(geom: BaseGeometry | None) -> Polygon | MultiPolygon:
    """Make `geom` valid (or empty), then drop any non-polygonal debris."""
    if geom is None or geom.is_empty:
        return Polygon()
    if not geom.is_valid:
        try:
            geom = make_valid(geom)
        except (GEOSException, ValueError):
            try:
                geom = geom.buffer(0)
            except (GEOSException, ValueError):
                return Polygon()
    return _polygonal(geom)


def _land_geometries(payload: Any) -> list[BaseGeometry]:
    """The geometry of every feature in a GeoJSON FeatureCollection / Feature / geometry."""
    if not isinstance(payload, dict):
        return []
    if payload.get("type") == "FeatureCollection":
        features = payload.get("features")
        if not isinstance(features, list):
            return []
        return [
            shape(feature["geometry"])
            for feature in features
            if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
        ]
    if payload.get("type") == "Feature":
        geometry = payload.get("geometry")
        return [shape(geometry)] if isinstance(geometry, dict) else []
    if "coordinates" in payload or payload.get("type") == "GeometryCollection":
        return [shape(payload)]
    return []


@cache
def load_land(path: str | Path) -> Polygon | MultiPolygon:
    """Union of every land feature in `path`, made valid; cached per path."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    geometries = [_repair(geom) for geom in _land_geometries(payload)]
    geometries = [geom for geom in geometries if not geom.is_empty]
    if not geometries:
        return Polygon()
    return _repair(unary_union(geometries))


def clip_to_water(poly: BaseGeometry | None, land: BaseGeometry | None) -> Polygon | MultiPolygon:
    """The part of `poly` that is water: `poly.difference(land)`, made valid.

    Empty input stays empty. Invalid input is repaired before the overlay so a single
    self-intersecting ring cannot break a snapshot run.
    """
    if poly is None or poly.is_empty:
        return Polygon()
    work = _repair(poly)
    if work.is_empty:
        return Polygon()
    if land is None or land.is_empty:
        return work
    try:
        clipped = work.difference(land)
    except (GEOSException, ValueError):
        try:
            clipped = work.buffer(0).difference(land.buffer(0))
        except (GEOSException, ValueError):
            return Polygon()
    return _repair(clipped)
