"""One process, one domain: the built SPA and the API share the FastAPI app (#15)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from darkfleet.api.app import app, reset_cache

INDEX = "<!doctype html><title>Dark Fleet</title><div id=root></div>"
SCRIPT = "console.log('dark fleet');"


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Each test points the app at its own directories."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal valid snapshot so /api/health works without the real data."""
    directory = tmp_path / "snapshot"
    directory.mkdir()
    (directory / "cases.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    return directory


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build the shape of `vite build`: index.html plus hashed assets."""
    directory = tmp_path / "dist"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(INDEX, encoding="utf-8")
    (directory / "assets" / "x.js").write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setenv("DARKFLEET_WEB_DIST", str(directory))
    return directory


@pytest.fixture
def client(snapshot: Path) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_root_serves_index(client: TestClient, dist: Path) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == INDEX


def test_asset_is_served_from_disk(client: TestClient, dist: Path) -> None:
    response = client.get("/assets/x.js")
    assert response.status_code == 200
    assert response.text == SCRIPT


def test_client_route_falls_back_to_index(client: TestClient, dist: Path) -> None:
    response = client.get("/live/BALTIC_WATCH")
    assert response.status_code == 200
    assert response.text == INDEX


def test_api_still_returns_json(client: TestClient, dist: Path) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_unknown_api_path_is_not_the_spa(client: TestClient, dist: Path) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_without_web_dist_root_is_404(
    client: TestClient, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DARKFLEET_WEB_DIST", raising=False)
    assert client.get("/").status_code == 404
    assert client.get("/live/BALTIC_WATCH").status_code == 404
    # The API is untouched when static hosting is off.
    assert client.get("/api/health").json()["ok"] is True
