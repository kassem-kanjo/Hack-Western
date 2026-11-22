"""Contract tests for the per-alert satellite check router (#268), including traversal guards.

Test fixtures only: the JSON mirrors what ``scripts/`` writes to disk, and the chips are a
two-by-two PNG made here rather than a real radar crop.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from darkfleet.api.app import app, reset_cache

ALERT_ID = "dark-230642000-1795306364"
NOT_READY = "no satellite check for this event yet"

# Test fixture: a completed check, every field the writer emits, served back untouched.
COMPUTED: dict[str, Any] = {
    "alert_id": ALERT_ID,
    "mmsi": 230642000,
    "name": "FINNMILL",
    "generated_at": "2026-11-22T04:00:00Z",
    "status": "computed",
    "dark_window": {"start": "2026-11-21T22:20:31Z", "end": None, "ongoing": True},
    "search": {"from": "2026-11-19T22:20:31Z", "to": "2026-11-22T04:00:00Z"},
    "area": {
        "center": [13.9, 55.05],
        "radius_km": 30.0,
        "reach_km": 94.1,
        "capped": True,
        "bbox": [13.6, 54.8, 14.2, 55.3],
    },
    "scene": {
        "id": "S1A_IW_GRDH_TEST",
        "time": "2026-11-21T05:12:00Z",
        "relation": "during",
        "minutes_after_switch_off": 170,
        "datatake_ids": ["S1A_TEST"],
    },
    "detections": [
        {
            "lat": 55.1,
            "lon": 13.9,
            "peak_db": 12.3,
            "contrast_db": 9.1,
            "matched_mmsi": None,
            "chip_url": f"/api/satcheck/{ALERT_ID}/chip/000.png",
        }
    ],
    "counts": {"detected": 14, "matched": 11, "not_broadcasting": 3},
    "ais": {"vessels_heard": 412, "recording": "BALTIC_WATCH", "coverage_note": "test coverage"},
    "notes": ["test fixture note"],
    "source": "Contains modified Copernicus Sentinel-1 data, via Microsoft Planetary Computer",
}

# Test fixture: the writer found no usable scene, so there is nothing to detect.
NO_SCENE: dict[str, Any] = {
    **COMPUTED,
    "status": "no_scene",
    "scene": None,
    "detections": [],
    "counts": None,
    "notes": ["no Sentinel-1 scene covers the dark window"],
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> Iterator[None]:
    """Each test points DARKFLEET_DATA at a new temp dir, so stale parses must not leak."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A snapshot directory with the empty satcheck folder the writer creates."""
    directory = tmp_path / "snapshot"
    (directory / "satcheck").mkdir(parents=True)
    monkeypatch.setenv("DARKFLEET_DATA", str(directory))
    return directory


def _png() -> bytes:
    """A real tiny PNG so the response can be checked byte for byte."""
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_check(directory: Path, payload: dict[str, Any], alert_id: str = ALERT_ID) -> Path:
    path = directory / "satcheck" / f"{alert_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _get(path: str) -> Any:
    with TestClient(app) as client:
        return client.get(path)


def test_computed_check_is_served_unchanged(data_dir: Path) -> None:
    _write_check(data_dir, COMPUTED)

    response = _get(f"/api/satcheck/{ALERT_ID}")

    assert response.status_code == 200
    assert response.json() == COMPUTED
    assert response.headers["cache-control"] == "no-cache"


def test_no_scene_check_is_served(data_dir: Path) -> None:
    _write_check(data_dir, NO_SCENE)

    response = _get(f"/api/satcheck/{ALERT_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_scene"
    assert body["scene"] is None and body["counts"] is None and body["detections"] == []


def test_missing_check_is_404_with_the_ui_detail(data_dir: Path) -> None:
    response = _get(f"/api/satcheck/{ALERT_ID}")

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_READY}


@pytest.mark.parametrize("text", ["{not json", "[]", '"text"'])
def test_malformed_check_is_500(data_dir: Path, text: str) -> None:
    (data_dir / "satcheck" / f"{ALERT_ID}.json").write_text(text, encoding="utf-8")

    response = _get(f"/api/satcheck/{ALERT_ID}")

    assert response.status_code == 500
    assert response.json() == {"detail": "satellite check file is malformed"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/satcheck/../etc",
        "/api/satcheck/dark-1-2/../../x",
        "/api/satcheck/DARK-1-2",
        "/api/satcheck/dark-1234567890123-1",
        "/api/satcheck/",
        "/api/satcheck/dark.1.2",
    ],
)
def test_invalid_alert_ids_are_404(data_dir: Path, path: str) -> None:
    # Nothing is written, so a 200 here could only come from reading outside DARKFLEET_DATA.
    assert _get(path).status_code == 404


def test_chip_is_served_as_png(data_dir: Path) -> None:
    _write_check(data_dir, COMPUTED)
    chips = data_dir / "satcheck" / ALERT_ID
    chips.mkdir()
    png = _png()
    (chips / "000.png").write_bytes(png)

    response = _get(f"/api/satcheck/{ALERT_ID}/chip/000.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.content == png


@pytest.mark.parametrize("name", ["../x.png", "0.png", "0000.png", "000.PNG", "000.png/"])
def test_invalid_chip_names_are_404(data_dir: Path, name: str) -> None:
    chips = data_dir / "satcheck" / ALERT_ID
    chips.mkdir()
    (chips / "000.png").write_bytes(_png())

    assert _get(f"/api/satcheck/{ALERT_ID}/chip/{name}").status_code == 404


def test_chip_for_a_missing_alert_is_404(data_dir: Path) -> None:
    assert _get(f"/api/satcheck/{ALERT_ID}/chip/000.png").status_code == 404
