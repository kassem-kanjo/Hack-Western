"""Read-only HTTP view of the edge unit's status file, for the console's API.

Runs on the Pi::

    python -m darkfleet.edge.status_http --status ~/darkfleet/edge_status.json --port 7071

``GET /edge_status.json`` and ``GET /latest.jpg`` are served straight from files the
supervisor and detector replace atomically. The browser never talks to the Pi,
``api/edge.py`` does.

Two demo-only write routes exist so both console buttons work when the edge is remote:
``POST /control/kill`` drops a ``control/kill_detector`` file and ``POST /control/stress``
drops ``control/stress_ai`` (body ``{"seconds": 10}``, default 10). The supervisor watches
those files; this server never signals a process itself. Each control route is limited to
one request per five seconds.

The listener defaults to loopback. Opening it to the LAN is deliberate (``--bind 0.0.0.0``
in ``scripts/edge_deploy.sh``) and should come with ``EDGE_STATUS_TOKEN``: when that env var
is set, every request must carry the same value as ``?token=``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STATUS_PATH = "/edge_status.json"
FRAME_PATH = "/latest.jpg"
DECISION_PATH = "/latest_decision.json"
AB_PATH = "/ab_result.json"
SIGHTINGS_PATH = "/sightings.json"
AB_NAME = "ab_result.json"
LATEST_FRAME_NAME = "latest.jpg"
DECISION_NAME = "latest_decision.json"
DEFAULT_BIND = "127.0.0.1"
TOKEN_ENV = "EDGE_STATUS_TOKEN"
CONTROL_DIR = "control"
KILL_FILE = "kill_detector"
STRESS_FILE = "stress_ai"
CONTROL_ROUTES = {"/control/kill": KILL_FILE, "/control/stress": STRESS_FILE}
CONTROL_MIN_INTERVAL_S = 5.0
DEFAULT_STRESS_S = 10.0
MAX_STRESS_S = 120.0
MAX_CONTROL_BODY = 1024
_NOT_FOUND = 404
_FORBIDDEN = 403
_BAD_REQUEST = 400
_TOO_MANY = 429
_UNAVAILABLE = 503


class _StatusServer(ThreadingHTTPServer):
    """Carries the status path and optional token so the handler does not parse the URL."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], status_path: Path, token: str | None = None) -> None:
        super().__init__(address, _StatusHandler)
        self.status_path = status_path
        # Fixed sibling paths, never derived from the request.
        self.latest_path = status_path.parent / LATEST_FRAME_NAME
        self.decision_path = status_path.parent / DECISION_NAME
        self.ab_path = status_path.parent / AB_NAME
        self.sightings_path = status_path.parent / "sightings.jsonl"
        self.control_dir = status_path.parent / CONTROL_DIR
        self.token = token.encode("utf-8") if token else None
        self.last_control: dict[str, float] = {}  # route -> monotonic time of the last write
        self.control_lock = threading.Lock()


class _StatusHandler(BaseHTTPRequestHandler):
    server_version = "DarkFleetEdgeStatus/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reply(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reply(send_body=False)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        request = urlsplit(self.path)
        if not self._authorized(request.query):
            self._send(_FORBIDDEN, b'{"error":"forbidden"}', True)
            return
        name = CONTROL_ROUTES.get(request.path)
        if name is None:
            self._send(_NOT_FOUND, b'{"error":"not found"}', True)
            return
        server = self.server
        now = time.monotonic()
        with server.control_lock:  # type: ignore[attr-defined]
            last = server.last_control.get(request.path)  # type: ignore[attr-defined]
            limited = last is not None and now - last < CONTROL_MIN_INTERVAL_S
            if not limited:
                server.last_control[request.path] = now  # type: ignore[attr-defined]
        if limited:
            assert last is not None
            wait = max(1, int(CONTROL_MIN_INTERVAL_S - (now - last)) + 1)
            body = json.dumps({"error": "rate limited", "retry_after_s": wait}).encode("utf-8")
            self._send(_TOO_MANY, body, True, extra_headers={"Retry-After": str(wait)})
            return

        seconds = DEFAULT_STRESS_S
        if name == STRESS_FILE:
            seconds, error = self._read_stress_body()
            if error is not None:
                self._send(_BAD_REQUEST, error, True)
                return
            payload = f"{seconds:g}".encode()
        else:
            payload = b"1"
        try:
            server.control_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
            target = server.control_dir / name  # type: ignore[attr-defined]
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, target)
        except OSError:
            self._send(_UNAVAILABLE, b'{"error":"cannot write control file"}', True)
            return
        body = json.dumps({"ok": True, "control": name, "seconds": seconds}).encode("utf-8")
        self._send(202, body, True)

    def _read_stress_body(self) -> tuple[float, bytes | None]:
        """Optional JSON body ``{"seconds": N}``; empty means the 10 s default."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return DEFAULT_STRESS_S, b'{"error":"bad content length"}'
        if length < 0 or length > MAX_CONTROL_BODY:
            return DEFAULT_STRESS_S, b'{"error":"bad content length"}'
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw.strip():
            return DEFAULT_STRESS_S, None
        try:
            parsed = json.loads(raw)
            seconds = float(parsed.get("seconds", DEFAULT_STRESS_S))
        except (ValueError, TypeError, AttributeError):
            return DEFAULT_STRESS_S, b'{"error":"seconds must be a number"}'
        if not 0.1 <= seconds <= MAX_STRESS_S:
            return DEFAULT_STRESS_S, b'{"error":"seconds out of range"}'
        return seconds, None

    def _reply(self, send_body: bool) -> None:
        request = urlsplit(self.path)
        if not self._authorized(request.query):
            self._send(_FORBIDDEN, b'{"error":"forbidden"}', send_body)
            return
        route = request.path
        if route == STATUS_PATH:
            self._serve_status(send_body)
        elif route == FRAME_PATH:
            self._serve_frame(send_body)
        elif route == DECISION_PATH:
            self._serve_decision(send_body)
        elif route == SIGHTINGS_PATH:
            self._serve_sightings(send_body)
        elif route == AB_PATH:
            self._serve_json_file(self.server.ab_path, send_body)  # type: ignore[attr-defined]
        else:
            self._send(_NOT_FOUND, b'{"error":"not found"}', send_body)

    def _authorized(self, query: str) -> bool:
        """Constant-time token check; a missing token env var leaves the server open."""
        expected = self.server.token  # type: ignore[attr-defined]
        if expected is None:
            return True
        supplied = parse_qs(query).get("token", [""])[0]
        try:
            return secrets.compare_digest(supplied.encode("utf-8"), expected)
        except UnicodeEncodeError:
            return False

    def _serve_status(self, send_body: bool) -> None:
        try:
            body = self.server.status_path.read_bytes()  # type: ignore[attr-defined]
            json.loads(body)  # a half-written file must not look like fresh status
        except (OSError, ValueError):
            self._send(_UNAVAILABLE, b'{"error":"status unavailable"}', send_body)
            return
        self._send(200, body, send_body, "application/json")

    def _serve_frame(self, send_body: bool) -> None:
        try:
            body = self.server.latest_path.read_bytes()  # type: ignore[attr-defined]
        except OSError:
            self._send(_NOT_FOUND, b'{"error":"no frame"}', send_body)
            return
        if not body:
            self._send(_NOT_FOUND, b'{"error":"no frame"}', send_body)
            return
        self._send(200, body, send_body, "image/jpeg")

    def _serve_sightings(self, send_body: bool) -> None:
        """The chain's verdict plus the last 20 sightings, newest first (read-only)."""
        from darkfleet.edge import sightings

        path = str(self.server.sightings_path)  # type: ignore[attr-defined]
        records, _torn = sightings.read_records(path)
        body = json.dumps(
            {"chain": sightings.verify(path), "sightings": list(reversed(records[-20:]))},
            sort_keys=True,
        ).encode("utf-8")
        self._send(200, body, send_body, "application/json")

    def _serve_json_file(self, path: Path, send_body: bool) -> None:
        """A small JSON file next to the status file (the A/B result), validated before serving."""
        try:
            body = path.read_bytes()
            json.loads(body)
        except (OSError, ValueError):
            self._send(_NOT_FOUND, b'{"error":"not available"}', send_body)
            return
        self._send(200, body, send_body, "application/json")

    def _serve_decision(self, send_body: bool) -> None:
        """The per-frame decision, validated like the status file so half a write is never served."""
        try:
            body = self.server.decision_path.read_bytes()  # type: ignore[attr-defined]
            json.loads(body)
        except (OSError, ValueError):
            self._send(_UNAVAILABLE, b'{"error":"decision unavailable"}', send_body)
            return
        self._send(200, body, send_body, "application/json")

    def _send(
        self,
        code: int,
        body: bytes,
        send_body: bool,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def _env_token() -> str | None:
    raw = os.environ.get(TOKEN_ENV, "").strip()
    return raw or None


def make_server(
    status_path: str | Path,
    host: str = DEFAULT_BIND,
    port: int = 7071,
    token: str | None = None,
) -> _StatusServer:
    """Bind the status server; port 0 picks a free port (used by tests)."""
    if token is None:
        token = _env_token()
    return _StatusServer((host, port), Path(status_path), token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.status_http")
    parser.add_argument("--status", default="edge_status.json", help="supervisor status JSON path")
    parser.add_argument(
        "--bind",
        default=DEFAULT_BIND,
        help=f"address to listen on (default {DEFAULT_BIND}; use 0.0.0.0 to open it)",
    )
    parser.add_argument("--port", type=int, default=7071)
    args = parser.parse_args(argv)
    server = make_server(args.status, args.bind, args.port)
    print(f"status_http: serving {args.status} on {args.bind}:{server.server_address[1]}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
