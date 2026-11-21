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

# Frame the relay expects: try to use real JPEG magic so nothing parses it as JSON by mistake.
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeStream:
    """Minimal stand-in for httpx's streaming response context manager."""

    def __init__(self, chunks: list[bytes] | None = None, error: Exception | None = None) -> None:
        self._chunks = chunks or []
        self._error = error

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def iter_bytes(self) -> Iterator[bytes]:
        yield from self._chunks


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch,
    calls: dict[str, Any],
    chunks: list[bytes] | None = None,
    error: Exception | None = None,
) -> None:
    """Replace httpx.stream with a fake that records the request and yields chunks."""

    def fake_stream(method: str, url: str, *, timeout: float, follow_redirects: bool) -> FakeStream:
        calls["method"] = method
        calls["url"] = url
        calls["timeout"] = timeout
        calls["follow_redirects"] = follow_redirects
        return FakeStream(chunks, error)

    monkeypatch.setattr(edge.httpx, "stream", fake_stream)


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
    remote = json.dumps({"state": "DEGRADED", "frames_total": 7, "detector_alive": False}).encode()
    _patch_stream(monkeypatch, calls, [remote])

    body = client.get("/api/edge/status").json()

    assert body["source"] == "pi"
    assert body["state"] == "DEGRADED"
    assert body["frames_total"] == 7
    assert calls == {
        "method": "GET",
        "url": "http://edge.local:7071/edge_status.json",
        "timeout": 1.0,
        "follow_redirects": False,
    }


def test_timeout_falls_back_to_the_local_file(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, error=httpx.TimeoutException("timed out"))

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
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, error=httpx.ConnectError("no route to host"))

    body = client.get("/api/edge/status").json()

    assert body["source"] == "local"
    assert body["state"] == "OK"


def test_status_over_the_byte_cap_is_502(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, [b"{" + b"x" * edge.STATUS_MAX_BYTES])

    response = client.get("/api/edge/status")

    assert response.status_code == 502
    assert response.json()["detail"] == "edge status too large"


def test_relay_deadline_falls_back_without_streaming_forever(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    monkeypatch.setattr(edge, "RELAY_DEADLINE_S", -1.0)
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, [b'{"state":"OK"}'])

    response = client.get("/api/edge/status")

    assert response.status_code == 200
    assert response.json()["source"] == "local"


def test_status_token_is_forwarded_when_configured(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    monkeypatch.setenv("EDGE_STATUS_TOKEN", "s3cret token")
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, [b'{"state":"OK"}'])

    assert client.get("/api/edge/status").json()["source"] == "pi"
    assert calls["url"] == "http://edge.local:7071/edge_status.json?token=s3cret+token"


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
    assert body["target"] == "local"
    assert isinstance(body["requested_at"], float)
    assert (local_status.parent / "control" / "kill_detector").is_file()


def _patch_post(monkeypatch: pytest.MonkeyPatch, calls: dict[str, Any], status_code: int = 202) -> None:
    """Replace httpx.post with a fake that records the control request."""

    def fake_post(url: str, *, json: Any = None, timeout: float, follow_redirects: bool) -> httpx.Response:
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        calls["follow_redirects"] = follow_redirects
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, json={"ok": status_code < 400}, request=request)

    monkeypatch.setattr(edge.httpx, "post", fake_post)


def test_kill_is_forwarded_to_the_pi_when_remote(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}
    _patch_post(monkeypatch, calls)

    response = client.post("/api/edge/kill")

    assert response.status_code == 202
    assert response.json()["target"] == "pi"
    assert calls["url"] == "http://edge.local:7071/control/kill"
    assert calls["json"] == {}
    assert calls["timeout"] == edge.CONTROL_TIMEOUT_S
    assert not (local_status.parent / "control").exists(), "a remote edge is never written locally"


def test_kill_forwarding_carries_the_token(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    monkeypatch.setenv("EDGE_STATUS_TOKEN", "s3cret")
    calls: dict[str, Any] = {}
    _patch_post(monkeypatch, calls)

    assert client.post("/api/edge/kill").status_code == 202
    assert calls["url"] == "http://edge.local:7071/control/kill?token=s3cret"


def test_kill_forwarding_failure_is_502(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}
    _patch_post(monkeypatch, calls, status_code=500)

    response = client.post("/api/edge/kill")

    assert response.status_code == 502
    assert response.json()["detail"] == "edge did not accept the control"
    assert not (local_status.parent / "control").exists()


def test_stress_is_403_when_controls_are_disabled(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "0")

    response = client.post("/api/edge/stress")

    assert response.status_code == 403
    assert not (local_status.parent / "control").exists()


def test_stress_writes_ten_seconds_locally(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")

    response = client.post("/api/edge/stress")

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["seconds"] == edge.STRESS_SECONDS
    assert (local_status.parent / "control" / "stress_ai").read_text() == "10"


def test_stress_is_forwarded_to_the_pi_when_remote(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    monkeypatch.setenv("EDGE_STATUS_TOKEN", "abc")
    calls: dict[str, Any] = {}
    _patch_post(monkeypatch, calls)

    response = client.post("/api/edge/stress")

    assert response.status_code == 202
    assert response.json()["target"] == "pi"
    assert calls["url"] == "http://edge.local:7071/control/stress?token=abc"
    assert calls["json"] == {"seconds": edge.STRESS_SECONDS}
    assert not (local_status.parent / "control").exists()


def test_stress_forwarding_failure_is_502(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_DEMO_CONTROLS", "1")
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}
    _patch_post(monkeypatch, calls, status_code=429)

    response = client.post("/api/edge/stress")

    assert response.status_code == 502
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
    _patch_stream(monkeypatch, calls, [b"\xff\xd8\xff\xe0remote-jpeg"])

    response = client.get("/api/edge/frame.jpg")

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff\xe0remote-jpeg"
    assert calls == {
        "method": "GET",
        "url": "http://edge.local:7071/latest.jpg",
        "timeout": 1.0,
        "follow_redirects": False,
    }


def test_frame_over_the_byte_cap_is_502(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, [b"\xff" * (edge.FRAME_MAX_BYTES + 1)])

    response = client.get("/api/edge/frame.jpg")

    assert response.status_code == 502
    assert response.json()["detail"] == "edge frame too large"


def test_frame_token_is_forwarded_when_configured(
    client: TestClient, local_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGE_HOST", "edge.local")
    monkeypatch.setenv("EDGE_STATUS_TOKEN", "abc")
    calls: dict[str, Any] = {}
    _patch_stream(monkeypatch, calls, [b"\xff\xd8jpeg"])

    assert client.get("/api/edge/frame.jpg").status_code == 200
    assert calls["url"] == "http://edge.local:7071/latest.jpg?token=abc"
