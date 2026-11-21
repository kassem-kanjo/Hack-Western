"""Contract tests for GET /api/edge/status: local file fallback and the Pi timeout path."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from darkfleet.api import edge
from darkfleet.api.app import app

STATUS = {
    "component": "supervisor",
    "state": "OK",
    "frames_total": 42,
    "detector_alive": True,
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def local_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "edge_status.json"
    path.write_text(json.dumps(STATUS), encoding="utf-8")
    monkeypatch.setenv("EDGE_STATUS_PATH", str(path))
    monkeypatch.delenv("EDGE_HOST", raising=False)
    monkeypatch.delenv("EDGE_DEMO_CONTROLS", raising=False)
    return path


def _write_latest(status_path: Path, body: bytes = b"\xff\xd8\xff\xe0fake-jpeg") -> Path:
    latest = status_path.parent / "latest.jpg"
    latest.write_bytes(body)
    return latest


def test_local_file_when_edge_host_is_unset(client: TestClient, local_status: Path) -> None:
    response = client.get("/api/edge/status")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local"
    assert body["state"] == "OK"
    assert body["frames_total"] == 42


def test_pi_status_wins_when_reachable(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"state": "DEGRADED", "frames_total": 7, "detector_alive": False}

    def fake_get(url: str, *, timeout: float) -> FakeResponse:
        calls["url"], calls["timeout"] = url, timeout
        return FakeResponse()

    monkeypatch.setattr(edge.httpx, "get", fake_get)

    body = client.get("/api/edge/status").json()

    assert body["source"] == "pi"
    assert body["state"] == "DEGRADED"
    assert body["frames_total"] == 7
    assert calls == {"url": "http://edge.local:7071/edge_status.json", "timeout": 1.0}


def test_timeout_falls_back_to_the_local_file(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")

    def slow_get(url: str, *, timeout: float) -> Any:
        raise httpx.TimeoutException(f"timed out after {timeout}s: {url}")

    monkeypatch.setattr(edge.httpx, "get", slow_get)

    response = client.get("/api/edge/status")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local"
    assert body["state"] == "OK"
    assert body["frames_total"] == 42


def test_http_error_also_falls_back(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")

    def failing_get(url: str, *, timeout: float) -> Any:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(edge.httpx, "get", failing_get)

    body = client.get("/api/edge/status").json()

    assert body["source"] == "local"
    assert body["state"] == "OK"


def test_missing_local_file_is_still_a_status(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_STATUS_PATH", str(tmp_path / "missing.json"))
    monkeypatch.delenv("EDGE_HOST", raising=False)

    response = client.get("/api/edge/status")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local"
    assert body["state"] == "UNKNOWN"


def test_status_reports_controls_and_frame_availability(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = client.get("/api/edge/status").json()
    assert body["controls"] is False
    assert body["frame_available"] is False

    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    _write_latest(local_status)

    body = client.get("/api/edge/status").json()
    assert body["controls"] is True
    assert body["frame_available"] is True


def test_kill_is_403_when_controls_are_disabled(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "0")

    response = client.post("/api/edge/kill")

    assert response.status_code == 403
    assert response.json()["detail"] == "controls disabled"
    assert not (local_status.parent / "control").exists()


def test_kill_writes_the_control_file_when_local(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")

    response = client.post("/api/edge/kill")

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["requested_at"], float)
    assert (local_status.parent / "control" / "kill_detector").is_file()


def test_kill_is_409_when_the_edge_is_remote(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    monkeypatch.setenv("EDGE_HOST", "edge.local")

    response = client.post("/api/edge/kill")

    assert response.status_code == 409
    assert not (local_status.parent / "control").exists()


def test_frame_is_404_when_there_is_none(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.get("/api/edge/frame.jpg")

    assert response.status_code == 404


def test_frame_serves_the_latest_jpeg(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"\xff\xd8\xff\xe0fake-jpeg-body"
    _write_latest(local_status, payload)

    response = client.get("/api/edge/frame.jpg")

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"


def test_frame_is_fetched_from_the_pi_when_configured(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}

    class FakeResponse:
        content = b"\xff\xd8\xff\xe0remote-jpeg"

        def raise_for_status(self) -> None:
            pass

    def fake_get(url: str, *, timeout: float) -> FakeResponse:
        calls["url"], calls["timeout"] = url, timeout
        return FakeResponse()

    monkeypatch.setattr(edge.httpx, "get", fake_get)

    response = client.get("/api/edge/frame.jpg")

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff\xe0remote-jpeg"
    assert calls == {"url": "http://edge.local:7071/latest.jpg", "timeout": 1.0}
