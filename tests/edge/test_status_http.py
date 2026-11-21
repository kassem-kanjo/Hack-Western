"""M-6: the Pi's status/frame server is loopback by default and token-gated when configured."""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from darkfleet.edge import status_http

STATUS = {"state": "OK", "frames_total": 3}


@pytest.fixture(autouse=True)
def _no_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDGE_STATUS_TOKEN", raising=False)


@pytest.fixture
def status_file(tmp_path: Path) -> Path:
    path = tmp_path / "edge_status.json"
    path.write_text(json.dumps(STATUS), encoding="utf-8")
    (tmp_path / "latest.jpg").write_bytes(b"\xff\xd8jpeg")
    return path


@contextlib.contextmanager
def _running(status_file: Path, token: str | None = "") -> Iterator[int]:
    # token="" means "explicitly no token"; None lets make_server read EDGE_STATUS_TOKEN.
    server = status_http.make_server(status_file, "127.0.0.1", 0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(port: int, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_status_is_served_without_a_token(status_file: Path) -> None:
    with _running(status_file) as port:
        code, body = _get(port, status_http.STATUS_PATH)

    assert code == 200
    assert json.loads(body) == STATUS


def test_default_bind_is_loopback(status_file: Path) -> None:
    server = status_http.make_server(status_file, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_token_is_required_on_both_routes_when_configured(status_file: Path) -> None:
    with _running(status_file, token="s3cret") as port:
        missing = _get(port, status_http.STATUS_PATH)
        wrong = _get(port, f"{status_http.STATUS_PATH}?token=nope")
        frame_missing = _get(port, status_http.FRAME_PATH)
        ok = _get(port, f"{status_http.STATUS_PATH}?token=s3cret")

    assert missing[0] == 403
    assert wrong[0] == 403
    assert frame_missing[0] == 403
    assert ok[0] == 200
    assert json.loads(ok[1]) == STATUS


def test_token_can_come_from_the_environment(status_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_STATUS_TOKEN", "from-env")
    with _running(status_file, token=None) as port:
        assert _get(port, status_http.STATUS_PATH)[0] == 403
        assert _get(port, f"{status_http.STATUS_PATH}?token=from-env")[0] == 200
