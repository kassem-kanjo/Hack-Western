"""Frame sources without a camera or OpenCV: parsing and the NetSource wire format."""

from __future__ import annotations

import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from darkfleet.edge import sources

FIXTURES = Path(__file__).parent / "fixtures"

# A synthetic JPEG: enough for sources.NetSource, which deliberately returns bytes undecoded.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


def _jpeg_header(width: int, height: int) -> bytes:
    """SOI + a valid SOF0 segment carrying the dimensions (no scan data)."""
    payload = b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x01\x11\x00" * 3
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", len(payload) + 2) + payload


def _send_frame(client: socket.socket, payload: bytes) -> None:
    client.sendall(sources.FRAME_HEADER.pack(len(payload)) + payload)


@pytest.mark.parametrize(
    ("spec", "kind", "target"),
    [
        ("dir:/tmp/frames", "dir", "/tmp/frames"),
        ("dir:relative/frames", "dir", "relative/frames"),
        ("camera:0", "camera", "0"),
        ("camera:12", "camera", "12"),
        ("net:7070", "net", "7070"),
        ("net:0", "net", "0"),
        ("/tmp/frames", "dir", "/tmp/frames"),
        ("  tests/edge/fixtures  ", "dir", "tests/edge/fixtures"),
    ],
)
def test_parse_source_spec(spec: str, kind: str, target: str) -> None:
    parsed = sources.parse_source_spec(spec)
    assert (parsed.kind, parsed.target) == (kind, target)


@pytest.mark.parametrize(
    "spec",
    ["", "   ", "dir:", "camera:", "camera:-1", "camera:front", "net:", "net:-1", "net:70000", "net:http"],
)
def test_parse_source_spec_rejects_bad_values(spec: str) -> None:
    with pytest.raises(ValueError):
        sources.parse_source_spec(spec)


def test_plain_path_opens_as_a_directory(tmp_path) -> None:
    (tmp_path / "frame_00.jpg").write_bytes(JPEG)
    plain = sources.open_source(str(tmp_path))
    prefixed = sources.open_source(f"dir:{tmp_path}")
    try:
        assert isinstance(plain, sources.DirSource)
        assert isinstance(prefixed, sources.DirSource)
    finally:
        plain.close()
        prefixed.close()


def test_camera_source_stays_lazy_without_opencv() -> None:
    # Constructing it must not import cv2 or touch the camera; only read() does.
    source = sources.open_source("camera:0")
    assert isinstance(source, sources.CameraSource)
    source.close()


def test_net_source_round_trip() -> None:
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        assert source.port > 0
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, JPEG)
            frame = source.read()
        assert frame is not None
        t_capture, payload = frame
        assert isinstance(t_capture, float)
        assert payload == JPEG
    finally:
        source.close()


def test_net_source_reads_back_to_back_frames() -> None:
    first = JPEG
    second = JPEG + b"second"
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, first)
            _send_frame(client, second)
            one, two = source.read(), source.read()
        assert one is not None and one[1] == first
        assert two is not None and two[1] == second
        assert one[0] <= two[0]
    finally:
        source.close()


def test_net_source_returns_none_without_a_client() -> None:
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        started = time.monotonic()
        assert source.read() is None
        assert time.monotonic() - started < 1.0
    finally:
        source.close()


def test_net_source_rejects_a_zero_length_frame() -> None:
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            client.sendall(sources.FRAME_HEADER.pack(0))
            assert source.read() is None
    finally:
        source.close()


def test_net_source_accepts_a_reconnect() -> None:
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, JPEG)
            assert source.read() is not None
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, JPEG)
            frame = source.read()
        assert frame is not None and frame[1] == JPEG
    finally:
        source.close()


def test_net_source_binds_loopback_by_default() -> None:
    source = sources.NetSource(0)
    try:
        assert source._sock.getsockname()[0] == "127.0.0.1"
    finally:
        source.close()


def test_net_source_rejects_the_wrong_token() -> None:
    source = sources.NetSource(0, host="127.0.0.1", token="s3cret")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, b"wrong")
            assert source.read() is None
        assert source._conn is None  # the peer was dropped before any frame was read
    finally:
        source.close()


def test_net_source_accepts_the_right_token_before_frames() -> None:
    source = sources.NetSource(0, host="127.0.0.1", token="s3cret")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, b"s3cret")
            _send_frame(client, JPEG)
            frame = source.read()
        assert frame is not None and frame[1] == JPEG
    finally:
        source.close()


def test_net_source_reads_the_token_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_NET_TOKEN", "from-env")
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            _send_frame(client, b"from-env")
            _send_frame(client, JPEG)
            assert source.read() is not None
    finally:
        source.close()


def test_net_source_enforces_a_total_frame_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "FRAME_DEADLINE_S", 0.3)
    source = sources.NetSource(0, host="127.0.0.1")
    stop = threading.Event()

    def slow_peer(client: socket.socket) -> None:
        try:
            client.sendall(sources.FRAME_HEADER.pack(1000))
            while not stop.is_set():
                client.sendall(b"x")
                time.sleep(0.05)
        except OSError:
            pass

    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            thread = threading.Thread(target=slow_peer, args=(client,), daemon=True)
            thread.start()
            started = time.monotonic()
            frame = source.read()
            elapsed = time.monotonic() - started
            stop.set()
            thread.join(timeout=2)
        assert frame is None
        assert elapsed < 1.0  # per-chunk timeouts alone would have kept reading for 1000 * 0.05s
        assert source._conn is None
    finally:
        source.close()


def test_net_source_short_wait_keeps_a_partial_frame_for_the_next_call() -> None:
    """The detector waits 0.5 s at a time and heartbeats in between: a frame split
    across two waits must still arrive whole."""
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5) as client:
            client.sendall(sources.FRAME_HEADER.pack(len(JPEG)) + JPEG[:10])
            started = time.monotonic()
            assert source.read(0.2) is None, "a partial frame is not a frame"
            assert time.monotonic() - started < 0.5
            assert source._conn is not None, "a short wait must not drop the peer"
            client.sendall(JPEG[10:])
            frame = source.read(0.5)
        assert frame is not None
        assert frame[1] == JPEG
    finally:
        source.close()


def test_net_source_short_wait_returns_within_the_budget() -> None:
    source = sources.NetSource(0, host="127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", source.port), timeout=5):
            started = time.monotonic()
            assert source.read(0.2) is None
            assert 0.1 < time.monotonic() - started < 1.0
    finally:
        source.close()


def test_image_dimensions_reads_jpeg_and_png_headers() -> None:
    assert sources.image_dimensions(_jpeg_header(640, 480)) == (640, 480)
    png = (FIXTURES / "frame_00.png").read_bytes()
    width, height = sources.image_dimensions(png)
    assert width > 0 and height > 0


def test_image_dimensions_rejects_unknown_bytes() -> None:
    assert sources.image_dimensions(b"\x00" * 32) is None
    assert sources.image_dimensions(b"\xff\xd8\xff\xc0") is None  # truncated SOF


def test_decode_frame_rejects_oversized_dimensions_before_decoding() -> None:
    with pytest.raises(ValueError, match="exceed"):
        sources.decode_frame(_jpeg_header(5000, 5000))


def test_decode_frame_rejects_bytes_without_a_readable_header() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        sources.decode_frame(b"not an image")
