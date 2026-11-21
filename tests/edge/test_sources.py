"""Frame sources without a camera or OpenCV: parsing and the NetSource wire format."""

from __future__ import annotations

import socket
import time

import pytest

from darkfleet.edge import sources

# A synthetic JPEG: enough for sources.NetSource, which deliberately returns bytes undecoded.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


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
