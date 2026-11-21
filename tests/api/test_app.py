"""Contract tests for the read-only API, driven by the real CaseDetail fixture."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from darkfleet.api.app import app, reset_cache

CASE_FIXTURE = Path(__file__).parents[1] / "fixtures" / "api" / "case.json"
CASE: dict[str, Any] = json.loads(CASE_FIXTURE.read_text(encoding="utf-8"))

GENERATED_AT = "2026-11-21T06:00:00Z"
TOTALS = {"gaps_scanned": 5624, "with_candidates": 1, "ruled_out": 0, "meetings": 0}

# Written out rather than imported so a schema drift fails the test.
SUMMARY_KEYS = {
    "id",
    "vessel",
    "off",
    "on",
    "hours",
    "score",
    "band",
    "status",
    "n_candidates",
    "n_meetings",
    "region_label",
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Every test gets its own DARKFLEET_DATA, so stale parses must not leak."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a one-case snapshot directory and point the app at it."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    payload = {"generated_at": GENERATED_AT, "totals": TOTALS, "cases": [CASE]}
    (directory / "cases.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    return directory


@pytest.fixture
def client(snapshot: Path) -> Iterator[TestClient]:
    """Use the context manager so startup/shutdown hooks run as in production."""
    with TestClient(app) as test_client:
        yield test_client


def test_list_shape_and_summary_keys(client: TestClient) -> None:
    response = client.get("/api/cases")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"generated_at", "totals", "cases"}
    assert body["generated_at"] == GENERATED_AT
    assert body["totals"] == TOTALS
    assert len(body["cases"]) == 1

    summary = body["cases"][0]
    assert set(summary) == SUMMARY_KEYS
    for key in SUMMARY_KEYS:
        assert summary[key] == CASE[key]


def test_status_filter(client: TestClient) -> None:
    candidates = client.get("/api/cases", params={"status": "candidate"}).json()["cases"]
    ruled_out = client.get("/api/cases", params={"status": "ruled_out"}).json()["cases"]
    everything = client.get("/api/cases", params={"status": "all"}).json()["cases"]

    assert [case["id"] for case in candidates] == [CASE["id"]]
    assert ruled_out == []
    assert [case["id"] for case in everything] == [CASE["id"]]


def test_limit_truncates(client: TestClient) -> None:
    assert client.get("/api/cases", params={"limit": 1}).json()["cases"] == [
        {key: CASE[key] for key in SUMMARY_KEYS}
    ]
    assert client.get("/api/cases", params={"limit": 0}).json()["cases"] == []


def test_detail_round_trip(client: TestClient) -> None:
    response = client.get(f"/api/cases/{CASE['id']}")
    assert response.status_code == 200
    assert response.json() == CASE


def test_detail_not_found(client: TestClient) -> None:
    response = client.get("/api/cases/gap:does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "case not found"}


def test_stats_fallback_when_file_missing(client: TestClient) -> None:
    response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json() == {"dark_hours_daily": [], "validation": None}


def test_health_reports_snapshot(client: TestClient, snapshot: Path) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "cases": 1, "source": str(snapshot)}
