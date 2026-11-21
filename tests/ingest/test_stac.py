"""Offline tests for the Copernicus STAC scene-time lookup (MockTransport, no network)."""

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from darkfleet.ingest import stac

DAY = date(2026, 10, 2)

RECORDED = {
    "type": "FeatureCollection",
    "context": {"matched": 1},
    "features": [
        {
            "type": "Feature",
            "id": "S1C_IW_GRDH_1SDV_20261002T082204_20261002T082235_009703_013531_1025_COG",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-43.1233, -25.4176],
                        [-42.6394, -23.5968],
                        [-45.0664, -23.0226],
                        [-45.5824, -24.8341],
                        [-43.1233, -25.4176],
                    ]
                ],
            },
            "properties": {
                "datetime": "2026-10-02T08:22:04.764038Z",
                "start_datetime": "2026-10-02T08:22:04.764038Z",
                "end_datetime": "2026-10-02T08:22:35.093921Z",
            },
        }
    ],
}

SCENE_TIME = datetime(2026, 10, 2, 8, 22, 4, 764038, tzinfo=UTC)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_recorded_scene_time(tmp_path):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=RECORDED)

    with _client(handler) as client:
        times = stac.scene_times(-24.97, -43.42, DAY, cache_dir=tmp_path, client=client)

    assert times == [SCENE_TIME]
    assert bodies[0]["collections"] == ["sentinel-1-grd"]
    assert bodies[0]["datetime"] == "2026-10-02T00:00:00Z/2026-10-02T23:59:59Z"
    assert bodies[0]["bbox"] == pytest.approx([-43.52, -25.07, -43.32, -24.87])


def test_cache_hit_makes_no_request(tmp_path):
    def first(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RECORDED)

    with _client(first) as client:
        expected = stac.scene_times(-24.97, -43.42, DAY, cache_dir=tmp_path, client=client)

    def network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network")

    with _client(network) as client:
        assert stac.scene_times(-24.97, -43.42, DAY, cache_dir=tmp_path, client=client) == expected


def test_point_outside_footprint_is_excluded(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RECORDED)

    with _client(handler) as client:
        times = stac.scene_times(-24.9, -45.6, DAY, cache_dir=tmp_path, client=client)

    assert times == []


def test_no_scene_is_cached_as_empty(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    with _client(handler) as client:
        assert stac.scene_times(-24.97, -43.42, DAY, cache_dir=tmp_path, client=client) == []

    assert list(tmp_path.glob("*.json"))


def test_pagination_follows_next_link(tmp_path):
    second = {
        "type": "Feature",
        "id": "second-scene",
        "geometry": RECORDED["features"][0]["geometry"],
        "properties": {"datetime": "2026-10-02T20:15:00Z", "start_datetime": "2026-10-02T20:15:00Z"},
    }
    page_one = {
        "type": "FeatureCollection",
        "features": [RECORDED["features"][0]],
        "links": [
            {
                "rel": "next",
                "method": "POST",
                "href": stac.STAC_SEARCH,
                "body": {"token": "p2"},
            }
        ],
    }
    page_two = {"type": "FeatureCollection", "features": [second]}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=page_two if body.get("token") == "p2" else page_one)

    with _client(handler) as client:
        times = stac.scene_times(-24.97, -43.42, DAY, cache_dir=tmp_path, client=client)

    assert times == [SCENE_TIME, datetime(2026, 10, 2, 20, 15, tzinfo=UTC)]


def test_http_error_raises_and_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(stac.time, "sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with _client(handler) as client, pytest.raises(httpx.HTTPStatusError):
        stac.scene_times(-24.97, -43.42, DAY, cache_dir=tmp_path, client=client)

    assert not list(tmp_path.glob("*.json"))
