"""QnxFileSource: the qnxcam file header, pixel formats, flip and de-duplication."""

from __future__ import annotations

import struct
import time
from pathlib import Path

import numpy as np
import pytest

from darkfleet.edge import sources

MAGIC = 0x4D414351


def _write_frame(
    path: Path,
    frametype: int,
    width: int,
    height: int,
    stride: int,
    body: bytes,
    *,
    timestamp_us: int = 1,
    uv_offset: int = 0,
) -> None:
    header = struct.pack(
        "<6IqqQ", MAGIC, frametype, height, width, stride, 0, uv_offset, timestamp_us, len(body)
    )
    path.write_bytes(header + body)


def _yuyv(width: int, height: int, pixels: list[tuple[int, int]]) -> bytes:
    """A YUYV buffer from two (Y, U/V) values per pair of pixels, neutral chroma."""
    row = bytearray()
    for y0, y1 in pixels:
        row += bytes([y0, 128, y1, 128])
    return bytes(row) * height


def test_parse_qnxfile_spec_and_flip() -> None:
    plain = sources.parse_source_spec("qnxfile:/tmp/qnxcam.frame")
    flipped = sources.parse_source_spec("qnxfile:/tmp/qnxcam.frame:flip")
    assert (plain.kind, plain.path, plain.flip) == ("qnxfile", "/tmp/qnxcam.frame", False)
    assert (flipped.kind, flipped.path, flipped.flip) == ("qnxfile", "/tmp/qnxcam.frame", True)
    with pytest.raises(ValueError):
        sources.parse_source_spec("qnxfile:")
    assert isinstance(sources.open_source("qnxfile:/tmp/qnxcam.frame"), sources.QnxFileSource)


def test_yuyv_file_decodes_to_bgr(tmp_path: Path) -> None:
    path = tmp_path / "qnxcam.frame"
    _write_frame(path, sources.FRAMETYPE_YUYV, 2, 2, 4, _yuyv(2, 2, [(128, 128)]))

    frame = sources.open_source(f"qnxfile:{path}").read(wait_s=0.5)

    assert frame is not None
    t_capture, img = frame
    assert isinstance(t_capture, float)
    assert img.shape == (2, 2, 3)
    assert np.allclose(img, 128, atol=2)


def test_flip_rotates_the_image_180(tmp_path: Path) -> None:
    path = tmp_path / "qnxcam.frame"
    # Left pixel bright, right pixel dark: a 180 rotation swaps them.
    _write_frame(path, sources.FRAMETYPE_YUYV, 2, 1, 4, _yuyv(2, 1, [(235, 16)]))

    upright = sources.open_source(f"qnxfile:{path}").read(wait_s=0.5)
    flipped = sources.open_source(f"qnxfile:{path}:flip").read(wait_s=0.5)

    assert upright is not None and flipped is not None
    up, down = upright[1], flipped[1]
    assert up[0, 0].mean() > 200 and up[0, 1].mean() < 50
    assert down[0, 0].mean() < 50 and down[0, 1].mean() > 200


def test_same_timestamp_is_never_returned_twice(tmp_path: Path) -> None:
    path = tmp_path / "qnxcam.frame"
    body = _yuyv(2, 2, [(128, 128)])
    _write_frame(path, sources.FRAMETYPE_YUYV, 2, 2, 4, body, timestamp_us=100)
    source = sources.QnxFileSource(path)

    assert source.read(wait_s=0.5) is not None
    assert source.read(wait_s=0.05) is None  # the file has not changed

    _write_frame(path, sources.FRAMETYPE_YUYV, 2, 2, 4, body, timestamp_us=101)
    assert source.read(wait_s=0.5) is not None


def test_missing_file_returns_within_the_wait_budget(tmp_path: Path) -> None:
    source = sources.QnxFileSource(tmp_path / "missing.frame")
    started = time.monotonic()
    assert source.read(wait_s=0.1) is None
    assert time.monotonic() - started < 0.5
    assert source.read() is None  # default budget is 0.5 s, not forever
    assert time.monotonic() - started < 1.0


def test_uyvy_nv12_and_rgb8888_decode(tmp_path: Path) -> None:
    uyvy = tmp_path / "uyvy.frame"
    _write_frame(uyvy, sources.FRAMETYPE_UYVY, 2, 1, 4, bytes([128, 128, 128, 128]))
    img = sources.decode_qnx_frame(uyvy.read_bytes())
    assert img.shape == (1, 2, 3)

    nv12 = tmp_path / "nv12.frame"
    y = bytes([76, 76, 76, 76])
    uv = bytes([128, 128])
    _write_frame(nv12, sources.FRAMETYPE_NV12, 2, 2, 2, y + uv, uv_offset=4)
    img = sources.decode_qnx_frame(nv12.read_bytes())
    assert img.shape == (2, 2, 3)

    rgb = tmp_path / "rgb.frame"
    _write_frame(rgb, sources.FRAMETYPE_RGB8888, 2, 1, 8, bytes([0, 0, 255, 0, 0, 0, 0, 0]))
    img = sources.decode_qnx_frame(rgb.read_bytes())
    assert img.shape == (1, 2, 3)
    assert img[0, 0, 2] == 255  # BGRA red


def test_bad_magic_and_unknown_frametype_raise(tmp_path: Path) -> None:
    path = tmp_path / "bad.frame"
    _write_frame(path, 99, 2, 2, 4, b"\x00" * 8)
    with pytest.raises(ValueError, match="frametype"):
        sources.decode_qnx_frame(path.read_bytes())

    _write_frame(path, sources.FRAMETYPE_YUYV, 2, 2, 4, b"\x00" * 8)
    data = bytearray(path.read_bytes())
    data[0:4] = b"NOPE"
    with pytest.raises(ValueError, match="magic"):
        sources.decode_qnx_frame(bytes(data))
    with pytest.raises(ValueError, match="header"):
        sources.decode_qnx_frame(b"short")
