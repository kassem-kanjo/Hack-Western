"""Native desktop shell for Dark Fleet (issue #86).

``python -m darkfleet.desktop`` serves the built SPA from a loopback uvicorn running in
a background thread, keeps ``data/snapshot/watch.json`` fresh, and opens the result in a
native pywebview window instead of a browser tab.

The server and the window share one process, so closing the window can stop the server
and the watch subprocess cleanly. Only the three ``EDGE_*`` settings are taken from the
repository's ``.env``: the API needs them to reach the Pi, the desktop shell needs nothing
else, and this module never prints their values.
"""

from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
"""The repository, resolved from this file so a bundle launched from Finder still finds it."""

WEB_DIR = ROOT / "web"
WEB_DIST = WEB_DIR / "dist"
WATCH_SCRIPT = ROOT / "scripts" / "build_watch.py"

EDGE_ENV_KEYS = ("EDGE_HOST", "EDGE_DEMO_CONTROLS", "EDGE_STATUS_TOKEN")
"""The only settings read from ``.env``; every other secret stays out of this process."""

WINDOW_TITLE = "Dark Fleet"
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
WINDOW_MIN_SIZE = (1100, 700)
WINDOW_BACKGROUND = "#0b1020"

WATCH_INTERVAL_S = 300
WATCH_PATTERN = "build_watch.py --loop"
"""``pgrep -f`` pattern; an already-running watch loop must not be started twice."""

SERVER_WAIT_S = 10.0
SHUTDOWN_WAIT_S = 5.0

BUILDING_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Dark Fleet</title>
<style>
  html, body { height: 100%; margin: 0; }
  body {
    display: flex; align-items: center; justify-content: center;
    background: #0b1020; color: #2dd4bf;
    font: 13px/1.4 -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
    letter-spacing: .14em; text-transform: uppercase;
  }
  .box { display: flex; flex-direction: column; align-items: center; gap: 18px; }
  .ring {
    width: 40px; height: 40px; border-radius: 50%;
    border: 2px solid #2dd4bf; border-top-color: transparent;
    animation: spin 1s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body><div class="box"><div class="ring"></div><p>Building&hellip;</p></div></body>
</html>
"""

BUILD_FAILED_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Dark Fleet</title>
<style>
  html, body { height: 100%; margin: 0; }
  body {
    display: flex; align-items: center; justify-content: center;
    background: #0b1020; color: #e2e8f0;
    font: 14px/1.6 -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  }
  p { max-width: 34em; text-align: center; }
  code { color: #2dd4bf; }
</style>
</head>
<body><p>Could not build the interface. Run <code>npm run build</code> in <code>web/</code>,
or see <code>logs/desktop.log</code>.</p></body>
</html>
"""


def pick_port(host: str = "127.0.0.1") -> int:
    """Return a free loopback port by letting the kernel choose one.

    Binding to port 0 and reading it back is the only race-free choice on macOS: looking
    for a gap and then binding it later can hand the port to another process in between.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def edge_env(path: Path | None = None) -> dict[str, str]:
    """Read the three ``EDGE_*`` values from ``.env`` and nothing else.

    The file is optional. Other keys in it (API tokens, database URLs) are deliberately
    dropped here so the desktop process cannot leak or depend on them.
    """
    from dotenv import dotenv_values

    source = path if path is not None else ROOT / ".env"
    try:
        values = dotenv_values(source)
    except OSError:
        return {}
    picked: dict[str, str] = {}
    for key in EDGE_ENV_KEYS:
        value = (values.get(key) or "").strip()
        if value:
            picked[key] = value
    return picked


def apply_env(values: Mapping[str, str], environ: MutableMapping[str, str] | None = None) -> None:
    """Copy the allowlisted values into the environment the API will read."""
    target = os.environ if environ is None else environ
    for key in EDGE_ENV_KEYS:
        value = values.get(key)
        if value:
            target[key] = value


def web_dist_ready(dist: Path = WEB_DIST) -> bool:
    """Whether ``vite build`` has produced something the API can serve."""
    return (dist / "index.html").is_file()


def build_web(root: Path = ROOT) -> None:
    """Run the production build in ``web/``; raises if npm or the build fails."""
    subprocess.run(["npm", "run", "build"], cwd=root / "web", check=True)


def watch_is_running(pattern: str = WATCH_PATTERN) -> bool:
    """Whether an equivalent watch loop is already alive, so we must not start another."""
    try:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def start_watch(root: Path = ROOT) -> tuple[subprocess.Popen[Any] | None, bool]:
    """Start ``build_watch.py --loop 300`` unless one is already running.

    Returns the process and whether this call started it. A process found by ``pgrep`` is
    returned as ``(None, False)``: it belongs to someone else and must survive shutdown.
    """
    if watch_is_running():
        return None, False
    process = subprocess.Popen(
        [sys.executable, str(root / "scripts" / "build_watch.py"), "--loop", str(WATCH_INTERVAL_S)],
        cwd=root,
    )
    return process, True


def start_server(port: int, host: str = "127.0.0.1") -> tuple[Any, threading.Thread]:
    """Run uvicorn on the loopback interface in a daemon thread."""
    import uvicorn

    from darkfleet.api.app import app

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="darkfleet-api", daemon=True)
    thread.start()
    return server, thread


def wait_until_serving(port: int, host: str = "127.0.0.1", timeout: float = SERVER_WAIT_S) -> bool:
    """Poll the API socket until it accepts a connection or the deadline passes."""
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex((host, port)) == 0:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def stop_process(process: subprocess.Popen[Any], timeout: float = SHUTDOWN_WAIT_S) -> None:
    """Terminate a child we started, escalating to SIGKILL if it ignores SIGTERM."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


@dataclass
class DesktopRuntime:
    """The processes a desktop run owns; only these are stopped when the window closes.

    ``watch`` is set only when this run started it. A watch loop found already running is
    intentionally left out, so closing the window does not kill a recorder-backed loop that
    was there before the app opened.
    """

    server: Any = None
    thread: threading.Thread | None = None
    watch: subprocess.Popen[Any] | None = None
    started_watch: bool = False

    def shutdown(self) -> None:
        """Ask uvicorn to stop, join its thread, and stop our own watch subprocess."""
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=SHUTDOWN_WAIT_S)
        if self.started_watch and self.watch is not None:
            stop_process(self.watch)


def open_window(
    url: str,
    *,
    dist: Path = WEB_DIST,
    root: Path = ROOT,
    webview_module: Any | None = None,
) -> None:
    """Open the native window and block until it is closed.

    A missing build opens the window immediately on a small "Building…" page; the build then
    runs in pywebview's worker thread and the real URL is loaded in the same window.
    """
    webview = webview_module if webview_module is not None else importlib.import_module("pywebview")
    needs_build = not web_dist_ready(dist)
    window = webview.create_window(
        WINDOW_TITLE,
        url=None if needs_build else url,
        html=BUILDING_HTML if needs_build else None,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
        background_color=WINDOW_BACKGROUND,
    )
    if not needs_build:
        webview.start()
        return

    def after_start() -> None:
        try:
            build_web(root)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"darkfleet desktop: web build failed: {exc}", file=sys.stderr)
            window.load_html(BUILD_FAILED_HTML)
            return
        window.load_url(url)

    webview.start(after_start)


def main() -> int:
    """Serve the app, open the window, and clean up when it closes."""
    apply_env(edge_env(ROOT / ".env"))
    os.environ["DARKFLEET_DATA"] = str(ROOT / "data" / "snapshot")
    os.environ["DARKFLEET_WEB_DIST"] = str(WEB_DIST)

    port = pick_port()
    url = f"http://127.0.0.1:{port}/?nointro#/"
    runtime = DesktopRuntime()
    try:
        runtime.server, runtime.thread = start_server(port)
        if not wait_until_serving(port):
            print("darkfleet desktop: API did not start", file=sys.stderr)
            return 1
        runtime.watch, runtime.started_watch = start_watch(ROOT)
        open_window(url)
    finally:
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
