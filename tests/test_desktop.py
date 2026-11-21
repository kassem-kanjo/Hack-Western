"""Tests for the desktop shell (#86): port choice, env filtering and owned cleanup."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from darkfleet import desktop


class FakeSocket:
    """Stand-in for a bound socket that reports the port the kernel chose."""

    def __init__(self, calls: list[tuple[str, int]], port: int = 54321) -> None:
        self._calls = calls
        self._port = port

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def bind(self, address: tuple[str, int]) -> None:
        self._calls.append(address)

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", self._port)


def test_pick_port_binds_loopback_port_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(desktop.socket, "socket", lambda *args: FakeSocket(calls))

    assert desktop.pick_port() == 54321
    assert calls == [("127.0.0.1", 0)]


def test_pick_port_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    port = desktop.pick_port()
    assert 1 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_edge_env_reads_only_the_three_allowed_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "EDGE_HOST=pi.local\n"
        "EDGE_DEMO_CONTROLS=1\n"
        "EDGE_STATUS_TOKEN=s3cret\n"
        "GFW_API_TOKEN=never-used\n"
        "DATABASE_URL=postgres://do-not-copy\n"
        "DARKFLEET_DATA=/somewhere/else\n",
        encoding="utf-8",
    )

    assert desktop.edge_env(env) == {
        "EDGE_HOST": "pi.local",
        "EDGE_DEMO_CONTROLS": "1",
        "EDGE_STATUS_TOKEN": "s3cret",
    }


def test_edge_env_missing_or_empty_is_empty(tmp_path: Path) -> None:
    assert desktop.edge_env(tmp_path / "missing.env") == {}
    blank = tmp_path / ".env"
    blank.write_text("EDGE_HOST=\nEDGE_DEMO_CONTROLS=   \n", encoding="utf-8")
    assert desktop.edge_env(blank) == {}


def test_apply_env_cannot_copy_other_keys() -> None:
    target: dict[str, str] = {}
    desktop.apply_env({"EDGE_HOST": "pi.local", "GFW_API_TOKEN": "leak"}, target)

    assert target == {"EDGE_HOST": "pi.local"}


def test_edge_env_never_prints_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = tmp_path / ".env"
    env.write_text("EDGE_STATUS_TOKEN=s3cret\n", encoding="utf-8")

    desktop.edge_env(env)

    captured = capsys.readouterr()
    assert "s3cret" not in captured.out + captured.err


class FakeServer:
    """Records the uvicorn shutdown flag."""

    def __init__(self) -> None:
        self.should_exit = False


class FakeThread:
    """Records the join timeout, so shutdown is observable without a real server."""

    def __init__(self) -> None:
        self.joined: float | None = None

    def join(self, timeout: float | None = None) -> None:
        self.joined = timeout


class FakeProcess:
    """A child process whose terminate/wait/kill calls are observable."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.alive = False
        return 0

    def kill(self) -> None:
        self.killed = True


def test_start_watch_does_not_duplicate_an_existing_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen must not run when a watch loop is already alive")

    monkeypatch.setattr(desktop, "watch_is_running", lambda: True)
    monkeypatch.setattr(desktop.subprocess, "Popen", explode)

    assert desktop.start_watch(tmp_path) == (None, False)


def test_start_watch_owns_the_process_it_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess()
    calls: dict[str, Any] = {}

    def fake_popen(argv: list[str], cwd: Path) -> FakeProcess:
        calls["argv"] = argv
        calls["cwd"] = cwd
        return process

    monkeypatch.setattr(desktop, "watch_is_running", lambda: False)
    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)

    started, owned = desktop.start_watch(tmp_path)

    assert (started, owned) == (process, True)
    assert calls["argv"][1] == str(tmp_path / "scripts" / "build_watch.py")
    assert calls["argv"][-2:] == ["--loop", "300"]
    assert calls["cwd"] == tmp_path


def test_shutdown_stops_the_server_and_the_watch_it_started() -> None:
    server = FakeServer()
    thread = FakeThread()
    process = FakeProcess()
    runtime = desktop.DesktopRuntime(server=server, thread=thread, watch=process, started_watch=True)

    runtime.shutdown()

    assert server.should_exit is True
    assert thread.joined == desktop.SHUTDOWN_WAIT_S
    assert process.terminated is True


def test_shutdown_leaves_a_watch_it_did_not_start_alone() -> None:
    """A loop found by pgrep belongs to someone else; closing the app must not kill it."""
    process = FakeProcess()
    runtime = desktop.DesktopRuntime(watch=process, started_watch=False)

    runtime.shutdown()

    assert process.terminated is False
    assert process.killed is False


def test_stop_process_escalates_when_sigterm_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stubborn(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired("build_watch", timeout or 0)
            self.alive = False
            return 0

    process = Stubborn()
    desktop.stop_process(process, timeout=0.01)

    assert process.terminated is True
    assert process.killed is True


class FakeWindow:
    """Records which page pywebview was asked to show."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.htmls: list[str] = []

    def load_url(self, url: str) -> None:
        self.urls.append(url)

    def load_html(self, html: str) -> None:
        self.htmls.append(html)


class FakeWebview:
    """A pywebview double: runs the start callback synchronously, no real window."""

    def __init__(self) -> None:
        self.window = FakeWindow()
        self.create_kwargs: dict[str, Any] = {}
        self.start_calls = 0

    def create_window(self, title: str, **kwargs: Any) -> FakeWindow:
        self.create_kwargs = {"title": title, **kwargs}
        return self.window

    def start(self, func: Any = None) -> None:
        self.start_calls += 1
        if func is not None:
            func()


def test_open_window_uses_the_dark_native_window(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    webview = FakeWebview()

    desktop.open_window("http://127.0.0.1:1234/?nointro#/", dist=dist, webview_module=webview)

    kwargs = webview.create_kwargs
    assert kwargs["title"] == "Dark Fleet"
    assert kwargs["width"] == 1440
    assert kwargs["height"] == 900
    assert kwargs["min_size"] == (1100, 700)
    assert kwargs["background_color"] == "#0b1020"
    assert kwargs["url"] == "http://127.0.0.1:1234/?nointro#/"
    assert webview.start_calls == 1


def test_open_window_builds_then_loads_the_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    (root / "web").mkdir(parents=True)
    dist = root / "web" / "dist"
    webview = FakeWebview()
    built: list[Path] = []

    def fake_build(path: Path) -> None:
        built.append(path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")

    monkeypatch.setattr(desktop, "build_web", fake_build)

    desktop.open_window("http://127.0.0.1:1234/?nointro#/", dist=dist, root=root, webview_module=webview)

    assert webview.create_kwargs["url"] is None
    assert "Building" in webview.create_kwargs["html"]
    assert built == [root]
    assert webview.window.urls == ["http://127.0.0.1:1234/?nointro#/"]
