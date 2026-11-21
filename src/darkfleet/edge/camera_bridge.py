"""Laptop-side camera bridge: capture a webcam and push JPEG frames to the Pi.

Run on the machine that has the camera::

    python -m darkfleet.edge.camera_bridge --camera 0 --to 192.168.1.50:7070 --fps 5 --width 640

Each frame is one 4-byte big-endian length followed by a JPEG, the same header
as ``protocol.py``. The Pi side is :class:`darkfleet.edge.sources.NetSource`.
The bridge reconnects with exponential backoff and reports fps and dropped
frames once a second. OpenCV is imported lazily so importing this module on a
board without it still works.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

from darkfleet.edge.sources import FRAME_HEADER, MAX_FRAME_BYTES

_INITIAL_BACKOFF_S = 0.5
_MAX_BACKOFF_S = 5.0
_CONNECT_TIMEOUT_S = 2.0
_SEND_TIMEOUT_S = 2.0
_REPORT_EVERY_S = 1.0
_JPEG_QUALITY = 80


def parse_target(value: str) -> tuple[str, int]:
    """Split ``host:port``; brackets are allowed for IPv6 (``[::1]:7070``)."""
    host, sep, raw_port = value.rpartition(":")
    if not sep or not host:
        raise ValueError(f"expected host:port, got {value!r}")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not raw_port.isdigit() or int(raw_port) > 65535:
        raise ValueError(f"invalid port in {value!r}")
    return host, int(raw_port)


def encode_jpeg(cv2, frame, quality: int) -> bytes | None:
    """Encode one BGR frame; None when OpenCV refuses the frame."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


def run(args: argparse.Namespace) -> int:
    import cv2  # lazy: this module must import on a board without OpenCV

    try:
        host, port = parse_target(args.to)
    except ValueError as exc:
        print(f"camera_bridge: {exc}", file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(args.camera)
    if args.width > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height > 0:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        print(f"camera_bridge: cannot open camera {args.camera}", file=sys.stderr)
        return 2

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    sock: socket.socket | None = None
    backoff = _INITIAL_BACKOFF_S
    total_sent = 0
    total_dropped = 0
    window_start = time.monotonic()
    window_sent = 0
    window_dropped = 0

    def report() -> None:
        nonlocal window_start, window_sent, window_dropped
        now = time.monotonic()
        elapsed = now - window_start
        if elapsed < _REPORT_EVERY_S:
            return
        fps = window_sent / elapsed
        print(
            f"camera_bridge: {fps:.1f} fps, {window_dropped} dropped "
            f"(sent {total_sent}, dropped {total_dropped})",
            file=sys.stderr,
            flush=True,
        )
        window_start, window_sent, window_dropped = now, 0, 0

    def drop(reason: str | None = None) -> None:
        nonlocal total_dropped, window_dropped
        total_dropped += 1
        window_dropped += 1
        if reason:
            print(f"camera_bridge: {reason}", file=sys.stderr, flush=True)

    try:
        while True:
            started = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                drop("camera read failed")
                time.sleep(0.05)
                report()
                continue

            if sock is None:
                try:
                    sock = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_S)
                    sock.settimeout(_SEND_TIMEOUT_S)
                    backoff = _INITIAL_BACKOFF_S
                    print(f"camera_bridge: connected to {host}:{port}", file=sys.stderr, flush=True)
                except OSError as exc:
                    drop(f"connect to {host}:{port} failed: {exc}; retrying in {backoff:.1f}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_S)
                    report()
                    continue

            payload = encode_jpeg(cv2, frame, args.jpeg_quality)
            if payload is None or not payload or len(payload) > MAX_FRAME_BYTES:
                drop("frame could not be encoded")
                report()
                continue

            try:
                sock.sendall(FRAME_HEADER.pack(len(payload)) + payload)
                total_sent += 1
                window_sent += 1
            except OSError as exc:
                drop(f"send failed: {exc}; reconnecting")
                sock.close()
                sock = None
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_S)

            report()
            spent = time.monotonic() - started
            if spent < interval:
                time.sleep(interval - spent)
    except KeyboardInterrupt:
        return 0
    finally:
        cap.release()
        if sock is not None:
            sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.camera_bridge")
    parser.add_argument("--camera", type=int, default=0, help="webcam index (default 0)")
    parser.add_argument("--to", required=True, help="Pi address as host:port, e.g. 192.168.1.50:7070")
    parser.add_argument("--fps", type=float, default=5.0, help="target frames per second")
    parser.add_argument("--width", type=int, default=640, help="requested capture width")
    parser.add_argument("--height", type=int, default=0, help="requested capture height (0 = camera default)")
    parser.add_argument("--jpeg-quality", type=int, default=_JPEG_QUALITY)
    args = parser.parse_args(argv)
    if args.fps < 0:
        print("camera_bridge: --fps must be >= 0", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
