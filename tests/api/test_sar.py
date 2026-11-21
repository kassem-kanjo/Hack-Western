"""Contract tests for the radar scan router (#90)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from darkfleet.api.app import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DARKFLEET_DATA", str(tmp_path))
    (tmp_path / "sar_scan.json").write_text(json.dumps({"scene_id": "S1X", "counts": {"detected": 1}}))
    (tmp_path / "chips").mkdir()
    (tmp_path / "chips" / "sar_001.png").write_bytes(PNG)
    (tmp_path / "secret.png").write_bytes(PNG)
    return TestClient(app)


def test_scan_served(client: TestClient) -> None:
    r = client.get("/api/sar/scan")
    assert r.status_code == 200 and r.json()["scene_id"] == "S1X"


def test_scan_missing_is_404_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DARKFLEET_DATA", str(tmp_path))
    r = TestClient(app).get("/api/sar/scan")
    assert r.status_code == 404 and "detail" in r.json()


def test_chip_served_as_png(client: TestClient) -> None:
    r = client.get("/api/sar/chip/sar_001.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG


def test_chip_unknown_and_traversal_are_404(client: TestClient) -> None:
    assert client.get("/api/sar/chip/nope.png").status_code == 404
    assert client.get("/api/sar/chip/..%2Fsecret.png").status_code == 404
    assert client.get("/api/sar/chip/%2E%2E/secret.png").status_code == 404
