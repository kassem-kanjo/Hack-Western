"""Real Sentinel-1 acquisition times for a radar cell, from the Copernicus Data Space STAC catalogue.

Free, no login. The GFW radar cell has only a date; this module returns the UTC acquisition times of the
scenes that cover the cell on that day. Responses are cached on disk so reruns make no network calls.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import Point, shape

STAC_SEARCH = "https://stac.dataspace.copernicus.eu/v1/search"
COLLECTION = "sentinel-1-grd"
HALF_BOX_DEG = 0.1
ROOT = Path(__file__).resolve().parents[3]  # repo root
CACHE_DIR = ROOT / "data" / "cache" / "stac"

MAX_PAGES = 20
MAX_TRIES = 4  # the first request plus three retries
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _request_body(lat: float, lon: float, day: date) -> dict[str, Any]:
    stamp = day.isoformat()
    return {
        "collections": [COLLECTION],
        "bbox": [lon - HALF_BOX_DEG, lat - HALF_BOX_DEG, lon + HALF_BOX_DEG, lat + HALF_BOX_DEG],
        "datetime": f"{stamp}T00:00:00Z/{stamp}T23:59:59Z",
        "limit": 100,
    }


def _cache_key(lat: float, lon: float, day: date) -> str:
    material = json.dumps(
        {"lat": round(lat, 4), "lon": round(lon, 4), "day": day.isoformat(), "collection": COLLECTION},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def _send(client: httpx.Client, method: str, url: str, body: dict | None = None) -> dict:
    """One request, retrying transient statuses; raises the httpx error once retries run out."""
    attempt = 0
    while True:
        attempt += 1
        response = client.request(method, url) if body is None else client.request(method, url, json=body)
        if response.status_code not in RETRY_STATUSES or attempt >= MAX_TRIES:
            response.raise_for_status()
            return response.json()
        time.sleep(2 * attempt)


def _fetch_features(client: httpx.Client, body: dict) -> list[dict]:
    """All raw features across the pages of one search, following ``rel: next`` links."""
    payload = _send(client, "POST", STAC_SEARCH, body)
    features = list(payload.get("features") or [])
    for _ in range(MAX_PAGES - 1):
        link = next((item for item in payload.get("links") or [] if item.get("rel") == "next"), None)
        if not link or not link.get("href"):
            break
        if link.get("body") is not None:
            payload = _send(client, str(link.get("method", "POST")).upper(), link["href"], link["body"])
        else:
            payload = _send(client, "GET", link["href"])
        features.extend(payload.get("features") or [])
    return features


def _contains(feature: dict, lat: float, lon: float) -> bool:
    geometry = feature.get("geometry")
    if not geometry:
        return False
    return shape(geometry).contains(Point(lon, lat))


def _feature_time(feature: dict) -> datetime | None:
    properties = feature.get("properties") or {}
    value = properties.get("datetime") or properties.get("start_datetime")
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def scene_times(
    lat: float,
    lon: float,
    day: date,
    *,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
) -> list[datetime]:
    """UTC acquisition times of the scenes covering ``(lat, lon)`` on ``day``, ascending and unique."""
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / f"{_cache_key(lat, lon, day)}.json"
    if cache_path.exists():
        features = json.loads(cache_path.read_text(encoding="utf-8")).get("features", [])
    else:
        body = _request_body(lat, lon, day)
        owned = client is None
        active = client or httpx.Client(timeout=60)
        try:
            features = _fetch_features(active, body)
        finally:
            if owned:
                active.close()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"request": body, "features": features}), encoding="utf-8")

    times: set[datetime] = set()
    for feature in features:
        if not _contains(feature, lat, lon):
            continue
        moment = _feature_time(feature)
        if moment is not None:
            times.add(moment)
    return sorted(times)
