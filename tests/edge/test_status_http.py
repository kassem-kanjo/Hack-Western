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


def _post(port: int, path: str, payload: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=payload if payload is not None else b"",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
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


# --- demo controls: the supervisor's files, never a signal ------------------


def test_control_kill_writes_the_supervisor_file(status_file: Path) -> None:
    with _running(status_file) as port:
        code, body = _post(port, "/control/kill")

    assert code == 202
    assert json.loads(body)["control"] == status_http.KILL_FILE
    control = status_file.parent / status_http.CONTROL_DIR / status_http.KILL_FILE
    assert control.read_bytes() == b"1"


def test_control_stress_defaults_to_ten_seconds(status_file: Path) -> None:
    with _running(status_file) as port:
        code, body = _post(port, "/control/stress")

    assert code == 202
    assert json.loads(body)["seconds"] == 10.0
    control = status_file.parent / status_http.CONTROL_DIR / status_http.STRESS_FILE
    assert control.read_bytes() == b"10"


def test_control_stress_reads_the_requested_seconds(status_file: Path) -> None:
    with _running(status_file) as port:
        code, body = _post(port, "/control/stress", json.dumps({"seconds": 3}).encode())

    assert code == 202
    assert json.loads(body)["seconds"] == 3.0
    control = status_file.parent / status_http.CONTROL_DIR / status_http.STRESS_FILE
    assert control.read_bytes() == b"3"


def test_control_stress_rejects_a_bad_body(status_file: Path) -> None:
    with _running(status_file) as port:
        code, _ = _post(port, "/control/stress", b'{"seconds": "forever"}')

    assert code == 400
    assert not (status_file.parent / status_http.CONTROL_DIR).exists()


def test_control_routes_need_the_token_when_configured(status_file: Path) -> None:
    with _running(status_file, token="s3cret") as port:
        denied = _post(port, "/control/kill")
        allowed = _post(port, "/control/kill?token=s3cret")

    assert denied[0] == 403
    assert allowed[0] == 202
    assert (status_file.parent / status_http.CONTROL_DIR / status_http.KILL_FILE).is_file()


def test_control_stress_is_rate_limited_to_one_per_five_seconds(status_file: Path) -> None:
    with _running(status_file) as port:
        first = _post(port, "/control/stress")
        second = _post(port, "/control/stress")
        # A different control route has its own limiter.
        kill = _post(port, "/control/kill")

    assert first[0] == 202
    assert second[0] == 429
    assert json.loads(second[1])["retry_after_s"] >= 1
    assert kill[0] == 202


def test_get_cannot_write_controls(status_file: Path) -> None:
    with _running(status_file) as port:
        code, _ = _get(port, "/control/kill")

    assert code == 404
    assert not (status_file.parent / status_http.CONTROL_DIR).exists()


def test_unknown_post_route_is_404(status_file: Path) -> None:
    with _running(status_file) as port:
        code, _ = _post(port, "/control/nuke")

    assert code == 404
