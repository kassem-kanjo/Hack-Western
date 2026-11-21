"""Read-only HTTP view of the edge unit's status file, for the console's API.

Runs on the Pi::

    python -m darkfleet.edge.status_http --status ~/darkfleet/edge_status.json --port 7071

Only ``GET /edge_status.json`` is served, straight from the file the supervisor
writes (which it replaces atomically). There are no CORS headers and no write
paths: the browser never talks to the Pi, ``api/edge.py`` does.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATUS_PATH = "/edge_status.json"
_NOT_FOUND = 404
_UNAVAILABLE = 503


class _StatusServer(ThreadingHTTPServer):
    """Carries the status path so the handler does not parse it from the URL."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], status_path: Path) -> None:
        super().__init__(address, _StatusHandler)
        self.status_path = status_path


class _StatusHandler(BaseHTTPRequestHandler):
    server_version = "DarkFleetEdgeStatus/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reply(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reply(send_body=False)

    def _reply(self, send_body: bool) -> None:
        if self.path.split("?", 1)[0] != STATUS_PATH:
            self._send(_NOT_FOUND, b'{"error":"not found"}', send_body)
            return
        try:
            body = self.server.status_path.read_bytes()  # type: ignore[attr-defined]
            json.loads(body)  # a half-written file must not look like fresh status
        except (OSError, ValueError):
            self._send(_UNAVAILABLE, b'{"error":"status unavailable"}', send_body)
            return
        self._send(200, body, send_body)

    def _send(self, code: int, body: bytes, send_body: bool) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def make_server(status_path: str | Path, host: str = "0.0.0.0", port: int = 7071) -> _StatusServer:
    """Bind the status server; port 0 picks a free port (used by tests)."""
    return _StatusServer((host, port), Path(status_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.status_http")
    parser.add_argument("--status", default="edge_status.json", help="supervisor status JSON path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7071)
    args = parser.parse_args(argv)
    server = make_server(args.status, args.host, args.port)
    print(f"status_http: serving {args.status} on {args.host}:{server.server_address[1]}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
