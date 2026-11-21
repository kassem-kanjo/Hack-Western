"""Microsoft Planetary Computer access for Sentinel-1 / Sentinel-2 windows (free, no account).

STAC search is anonymous; assets are cloud-optimised GeoTIFFs behind a short-lived SAS token that
``/api/sas/v1/token/{collection}`` hands out without login. Only the requested window is fetched
(HTTP range requests through rasterio). Needs the optional ``sat`` extra (rasterio, pillow).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import numpy as np

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
}


@dataclass(frozen=True)
class Item:
    id: str
    collection: str
    time: datetime
    bbox: tuple[float, float, float, float]
    cloud: float | None
    assets: dict[str, str]  # name -> href (unsigned)


_TOKENS: dict[str, tuple[float, str]] = {}


def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request, retrying rate limits and server errors with a growing pause."""
    for attempt in range(5):
        response = httpx.request(method, url, timeout=60, **kwargs)
        if response.status_code not in (429, 500, 502, 503, 504) or attempt == 4:
            response.raise_for_status()
            return response
        time.sleep(2.0 * (attempt + 1))
    raise AssertionError("unreachable")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def search(
    collection: str,
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    *,
    limit: int = 100,
    query: dict[str, Any] | None = None,
) -> list[Item]:
    """Items of ``collection`` intersecting ``bbox`` in ``[start, end]``, newest first."""
    body: dict[str, Any] = {
        "collections": [collection],
        "bbox": list(bbox),
        "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
        "limit": limit,
        "sortby": [{"field": "datetime", "direction": "desc"}],
    }
    if query:
        body["query"] = query
    response = _request("POST", STAC, json=body)
    items = []
    for f in response.json().get("features", []):
        props = f["properties"]
        items.append(
            Item(
                id=f["id"],
                collection=collection,
                time=_parse_time(props["datetime"]),
                bbox=tuple(f["bbox"]),
                cloud=props.get("eo:cloud_cover"),
                assets={k: v["href"] for k, v in f["assets"].items() if "href" in v},
            )
        )
    return items


def sign(collection: str, href: str) -> str:
    """Append an anonymous read-only SAS token for the collection's storage container (cached)."""
    cached = _TOKENS.get(collection)
    if cached is None or cached[0] < time.time() + 120:
        token = _request("GET", SAS.format(collection=collection)).json()
        expiry = datetime.fromisoformat(token["msft:expiry"].replace("Z", "+00:00")).timestamp()
        cached = _TOKENS[collection] = (expiry, token["token"])
    return f"{href}?{cached[1]}"


def _read_band(href: str, window: Any) -> np.ndarray:
    import rasterio

    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        return ds.read(1, window=window)


@dataclass(frozen=True)
class Window:
    data: np.ndarray  # float32, nodata = NaN
    transform: Any  # affine, pixel -> CRS
    crs: Any


def read_window(
    href: str, bbox: tuple[float, float, float, float], *, threads: int = 6, nodata: float | None = None
) -> Window | None:
    """Read the part of a GeoTIFF inside the lon/lat ``bbox`` (None if there is none), in bands."""
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window as RWindow
    from rasterio.windows import from_bounds

    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        left, bottom, right, top = transform_bounds("EPSG:4326", ds.crs, *bbox, densify_pts=21)
        win = from_bounds(left, bottom, right, top, transform=ds.transform).round_offsets().round_lengths()
        win = win.intersection(RWindow(0, 0, ds.width, ds.height))
        transform: Affine = ds.window_transform(win)
        crs = ds.crs
        nd = ds.nodata if nodata is None else nodata
        height, width = int(win.height), int(win.width)
        row0, col0 = int(win.row_off), int(win.col_off)
    if height <= 0 or width <= 0:
        return None
    step = max(1, -(-height // threads))
    bands = [RWindow(col0, row0 + r, width, min(step, height - r)) for r in range(0, height, step)]
    with ThreadPoolExecutor(threads) as pool:
        parts = list(pool.map(lambda w: _read_band(href, w), bands))
    data = np.concatenate(parts, axis=0).astype(np.float32)
    if nd is not None:
        data[data == nd] = np.nan
    return Window(data=data, transform=transform, crs=crs)


def group_datatake(items: list[Item], gap: timedelta = timedelta(minutes=3)) -> list[Item]:
    """The newest acquisition: the first item plus its neighbours within ``gap`` (adjacent slices)."""
    if not items:
        return []
    items = sorted(items, key=lambda i: i.time, reverse=True)
    out = [items[0]]
    for item in items[1:]:
        if abs((out[-1].time - item.time).total_seconds()) <= gap.total_seconds():
            out.append(item)
        else:
            break
    return out
