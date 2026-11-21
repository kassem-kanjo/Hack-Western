"""Offline tests for the GFW HTTP client (httpx.MockTransport, no network, no token)."""

import json
from datetime import date

import httpx
import pytest

from darkfleet.ingest.gfw import GfwClient, GfwError
from darkfleet.ingest.regions import HORMUZ

START = date(2026, 9, 1)
END = date(2026, 9, 3)


def _gap_entry(entry_id: str) -> dict:
    return {
        "id": entry_id,
        "start": "2026-09-01T00:00:00.000Z",
        "end": "2026-09-02T00:00:00.000Z",
        "vessel": {"id": "v-1", "ssvid": "123456789", "name": "A", "flag": "PAN", "type": "fishing"},
        "gap": {
            "offPosition": {"lat": 1.0, "lon": 2.0},
            "onPosition": {"lat": 3.0, "lon": 4.0},
            "durationHours": 24.0,
        },
    }


def _empty_sar() -> dict:
    return {"entries": [{"public-global-sar-presence:v5.0": []}]}


def _client(tmp_path, handler, **kwargs) -> GfwClient:
    return GfwClient(
        "test-token",
        tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_pagination_follows_next_offset(tmp_path):
    seen: list[int] = []
    pages = {
        0: {"total": 3, "nextOffset": 200, "entries": [_gap_entry("g0")]},
        200: {"total": 3, "nextOffset": 400, "entries": [_gap_entry("g1")]},
        400: {"total": 3, "nextOffset": None, "entries": [_gap_entry("g2")]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        seen.append(offset)
        return httpx.Response(201, json=pages[offset])

    gaps = _client(tmp_path, handler).gaps(HORMUZ, START, END)

    assert seen == [0, 200, 400]
    assert [gap.id for gap in gaps] == ["g0", "g1", "g2"]


def test_retry_on_429_and_5xx_then_succeeds(tmp_path):
    statuses = iter([429, 503, 201])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 201:
            return httpx.Response(201, json={"total": 1, "nextOffset": None, "entries": [_gap_entry("g0")]})
        return httpx.Response(status, json={"error": "try again"})

    gaps = _client(tmp_path, handler, sleep=sleeps.append).gaps(HORMUZ, START, END)

    assert [gap.id for gap in gaps] == ["g0"]
    assert sleeps == [1.0, 2.0]


def test_retry_exhausted_raises_gfw_error(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(GfwError) as excinfo:
        _client(tmp_path, handler, sleep=lambda _: None).gaps(HORMUZ, START, END)

    assert calls == 4
    assert "500" in str(excinfo.value)
    assert "/events" in str(excinfo.value)


def test_forbidden_raises_immediately(tmp_path):
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(GfwError) as excinfo:
        _client(tmp_path, handler, sleep=sleeps.append).gaps(HORMUZ, START, END)

    assert calls == 1
    assert sleeps == []
    assert "403" in str(excinfo.value)
    assert "/events" in str(excinfo.value)


def test_cache_hit_makes_no_http_request(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json={"total": 1, "nextOffset": None, "entries": [_gap_entry("g0")]})

    client = _client(tmp_path, handler)
    first = client.gaps(HORMUZ, START, END)
    second = client.gaps(HORMUZ, START, END)

    assert calls == 1
    assert first == second


def test_token_never_written_to_cache(tmp_path):
    token = "super-secret-gfw-token-value"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"total": 0, "nextOffset": None, "entries": []})

    client = GfwClient(token, tmp_path / "cache", transport=httpx.MockTransport(handler))
    client.gaps(HORMUZ, START, END)

    files = [path for path in (tmp_path / "cache").rglob("*") if path.is_file()]
    assert files
    for path in files:
        assert token not in path.read_text(encoding="utf-8")


def test_events_request_shape(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"total": 0, "nextOffset": None, "entries": []})

    _client(tmp_path, handler).gaps(HORMUZ, START, END)

    request = captured[0]
    assert request.headers["authorization"] == "Bearer test-token"
    assert dict(request.url.params) == {"limit": "200", "offset": "0"}
    assert json.loads(request.content) == {
        "datasets": ["public-global-gaps-events:latest"],
        "startDate": "2026-09-01",
        "endDate": "2026-09-03",
        "geometry": HORMUZ,
    }


def test_sar_request_shape_unmatched(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json=_empty_sar())

    _client(tmp_path, handler).sar_cells(HORMUZ, START, END, unmatched_only=True)

    request = captured[0]
    params = request.url.params
    assert params["spatial-resolution"] == "HIGH"
    assert params["temporal-resolution"] == "DAILY"
    assert params["datasets[0]"] == "public-global-sar-presence:latest"
    assert params["date-range"] == "2026-09-01,2026-09-03"
    assert params["format"] == "JSON"
    assert params["filters[0]"] == "matched='false'"
    assert json.loads(request.content) == {"geojson": HORMUZ}


def test_sar_request_shape_matched_omits_filter(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json=_empty_sar())

    _client(tmp_path, handler).sar_cells(HORMUZ, START, END, unmatched_only=False)

    assert "filters[0]" not in captured[0].url.params


def test_empty_token_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        GfwClient("", tmp_path / "cache")
