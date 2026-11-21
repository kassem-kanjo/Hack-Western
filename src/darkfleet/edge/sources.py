"""Frame sources for the untrusted detector: a directory, a camera or the network.

Every source returns ``(t_capture, frame)`` where ``t_capture`` is a
``time.monotonic()`` value taken when the frame was read. A frame is either a
decoded BGR ``numpy`` image or the encoded JPEG bytes that arrived over the
network; :func:`decode_frame` imports OpenCV only when it actually has to decode
one, so this module still imports on a QNX board without OpenCV.

``--source`` accepts ``dir:<path>``, ``camera:<index>``, ``net:<port>`` and
``qnxfile:<path>[:flip]``. A plain path means a directory, which keeps the
original detector command line working. A ``net:`` listener binds loopback
unless the detector is given ``--net-bind``, and optionally requires
``EDGE_NET_TOKEN`` as the first frame. A ``qnxfile:`` source polls the newest
frame that the ``qnxcam`` helper wrote (a 48-byte header followed by raw
pixels) and only returns it once, when its timestamp changes.
"""

from __future__ import annotations

import os
import secrets
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp"})
# Same header as protocol.py: 4-byte big-endian unsigned length.
FRAME_HEADER = struct.Struct("!I")
MAX_FRAME_BYTES = 8 << 20  # a 640x480 JPEG is tens of KiB; cap a hostile peer
# The token rides in the same length-prefixed framing as a frame; it is short.
MAX_TOKEN_BYTES = 4096
DEFAULT_NET_BIND = "127.0.0.1"  # opt in to 0.0.0.0 with --net-bind / EDGE_NET_BIND
NET_TOKEN_ENV = "EDGE_NET_TOKEN"
# A peer that stops making progress for this long mid-frame is dropped. Short-wait
# readers keep their partial frame between calls; the total budget still stops a peer
# dripping one byte every 0.9 s from holding the detector forever.
FRAME_DEADLINE_S = 2.0
# cv2.imdecode allocates from the image header, so a tiny hostile file can claim a huge
# bitmap. Refuse oversized headers before OpenCV ever sees the bytes.
MAX_IMAGE_DIMENSION = 4096
_ACCEPT_TIMEOUT_S = 0.2
_RECV_TIMEOUT_S = 1.0
_RECV_CHUNK = 1 << 16

# qnxcam's file layout (see edge/qnxcam/qnxcam.c and qnxcam_read.py): six little-endian
# u32 fields, then int64 uv_offset, int64 timestamp_us and uint64 nbytes, then raw pixels.
QNX_MAGIC = 0x4D414351  # the bytes "QCAM" read as a little-endian u32
QNX_HEADER = struct.Struct("<6IqqQ")
QNX_HEADER_SIZE = QNX_HEADER.size  # 48
QNX_POLL_S = 0.02
# A quiet file must not hold the detector for longer than this in one call: the supervisor
# kills a detector that stops heartbeating, and the detector heartbeats between waits.
QNX_WAIT_S = 0.5
FRAMETYPE_NV12 = 1
FRAMETYPE_RGB8888 = 2
FRAMETYPE_UYVY = 8
FRAMETYPE_YUYV = 14
_QNX_BYTES_PER_PIXEL = {FRAMETYPE_YUYV: 2, FRAMETYPE_UYVY: 2, FRAMETYPE_RGB8888: 4}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"
# Start-Of-Frame markers carry the true dimensions; C4/C8/CC are not SOF markers.
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})

FrameData = np.ndarray | bytes
Frame = tuple[float, FrameData]
SourceKind = Literal["dir", "camera", "net", "qnxfile"]


def list_images(root: Path) -> list[Path]:
    """Image files directly inside `root`, in deterministic name order."""
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


class FrameSource(Protocol):
    """What the detector needs: pull one frame, rewind, release."""

    def read(self, wait_s: float | None = None) -> Frame | None:
        """Return the next frame, or None when none is available right now.

        ``wait_s`` bounds how long the call may block, so the detector can send a
        heartbeat and come back. ``None`` keeps the source's own default budget.
        """
        ...

    def restart(self) -> None:
        """Rewind a finite source, or do nothing for a live one."""
        ...

    def close(self) -> None:
        """Release the camera, socket or file handles this source owns."""
        ...


class DirSource:
    """Replays the images in a directory, one per call, then reports exhaustion."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        if not self._root.is_dir():
            raise FileNotFoundError(f"not an image directory: {root}")
        self._paths = list_images(self._root)
        if not self._paths:
            raise FileNotFoundError(f"no images in {root}")
        self._next = 0

    def read(self, wait_s: float | None = None) -> Frame | None:
        if self._next >= len(self._paths):
            return None
        import cv2

        path = self._paths[self._next]
        self._next += 1
        img = cv2.imread(str(path))
        if img is None:
            raise OSError(f"cannot read image: {path}")
        return time.monotonic(), img

    def restart(self) -> None:
        self._next = 0

    def close(self) -> None:
        pass


class CameraSource:
    """A USB or CSI camera through OpenCV. cv2 is imported only on first read."""

    def __init__(self, index: int) -> None:
        if index < 0:
            raise ValueError(f"camera index must be >= 0, got {index}")
        self._index = index
        self._cap = None
        self._cv2 = None

    def _open(self) -> None:
        if self._cap is not None:
            return
        import cv2

        cap = cv2.VideoCapture(self._index)
        if not cap.isOpened():
            cap.release()
            raise OSError(f"cannot open camera {self._index}")
        self._cv2, self._cap = cv2, cap

    def read(self, wait_s: float | None = None) -> Frame | None:
        self._open()
        assert self._cap is not None
        ok, img = self._cap.read()
        if not ok:
            return None
        return time.monotonic(), img

    def restart(self) -> None:
        if self._cap is not None:
            assert self._cv2 is not None
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, 0)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class NetSource:
    """TCP server for length-prefixed JPEG frames pushed by ``camera_bridge``.

    One client at a time: ``camera_bridge`` is the only client, and accepting a
    second connection while a frame is half-read would corrupt the stream.
    A disconnect drops the connection and the next ``read`` accepts a new one,
    so the bridge can reconnect without restarting the detector.

    The listener defaults to loopback and must be opened deliberately with
    ``--net-bind``. When ``EDGE_NET_TOKEN`` is set, a connection's first framed
    message must be that token or it is dropped before any frame is read.
    """

    def __init__(self, port: int, host: str = DEFAULT_NET_BIND, *, token: str | None = None) -> None:
        if not 0 <= port <= 65535:
            raise ValueError(f"port out of range: {port}")
        raw_token = token if token is not None else os.environ.get(NET_TOKEN_ENV, "")
        self._token = raw_token.encode("utf-8") if raw_token else None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        self._sock.settimeout(_ACCEPT_TIMEOUT_S)
        self.port: int = self._sock.getsockname()[1]
        self._conn: socket.socket | None = None
        self._authenticated = False
        # Bytes of the message being read, kept across short-wait calls so a small
        # heartbeat-sized wait never truncates a frame or corrupts the stream.
        self._buf = bytearray()
        self._last_progress = 0.0

    def read(self, wait_s: float | None = None) -> Frame | None:
        """One complete JPEG frame. None means no client, a timeout or a dropped peer.

        ``wait_s`` bounds this call (the detector asks for 0.5 s so it can heartbeat
        while the bridge is quiet). A partial frame is kept between calls; a peer that
        makes no progress for ``FRAME_DEADLINE_S`` is dropped, as it always was.
        """
        strict = wait_s is None
        budget = FRAME_DEADLINE_S if strict else max(0.0, wait_s)
        deadline = time.monotonic() + budget
        while True:
            payload = self._next_message(deadline, strict)
            if payload is None:
                return None
            if not self._authenticated:
                assert self._token is not None
                if len(payload) > MAX_TOKEN_BYTES or not secrets.compare_digest(payload, self._token):
                    self._drop()
                    if strict:
                        return None
                    continue
                self._authenticated = True
                continue
            return time.monotonic(), payload

    def _next_message(self, deadline: float, strict: bool) -> bytes | None:
        """One length-prefixed message, or None when the budget/peer gives out."""
        while True:
            if self._conn is None and not self._accept(deadline):
                return None
            if len(self._buf) < FRAME_HEADER.size:
                if not self._fill(FRAME_HEADER.size, deadline, strict):
                    if self._conn is None and time.monotonic() < deadline:
                        continue  # the peer went away; a reconnect may already be queued
                    return None
            (length,) = FRAME_HEADER.unpack_from(self._buf, 0)
            if length == 0 or length > MAX_FRAME_BYTES:
                self._drop()
                if strict:
                    return None
                continue
            end = FRAME_HEADER.size + length
            if len(self._buf) < end and not self._fill(end, deadline, strict):
                if self._conn is None and time.monotonic() < deadline:
                    continue
                return None
            payload = bytes(self._buf[FRAME_HEADER.size : end])
            del self._buf[:end]
            return payload

    def _fill(self, size: int, deadline: float, strict: bool) -> bool:
        """Read until ``size`` bytes are buffered. False on timeout, EOF or a drop."""
        assert self._conn is not None
        while len(self._buf) < size:
            left = deadline - time.monotonic()
            if left <= 0:
                if strict:  # a one-shot read that timed out is a broken stream
                    self._drop()
                return False
            if self._last_progress and time.monotonic() - self._last_progress > FRAME_DEADLINE_S:
                self._drop()
                return False
            self._conn.settimeout(min(_RECV_TIMEOUT_S, left))
            try:
                chunk = self._conn.recv(min(size - len(self._buf), _RECV_CHUNK))
            except TimeoutError:
                continue  # the loop re-checks the budget
            except OSError:
                self._drop()
                return False
            if not chunk:
                self._drop()
                return False
            self._buf.extend(chunk)
            self._last_progress = time.monotonic()
        return True

    def _accept(self, deadline: float) -> bool:
        if self._conn is not None:
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        self._sock.settimeout(min(_ACCEPT_TIMEOUT_S, left))
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return False
        conn.settimeout(_RECV_TIMEOUT_S)
        self._conn = conn
        self._authenticated = self._token is None
        self._buf.clear()
        self._last_progress = time.monotonic()
        return True

    def _drop(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._authenticated = False
        self._buf.clear()

    def restart(self) -> None:
        """Keep the connection: reconnecting is the bridge's job, not the detector's."""

    def close(self) -> None:
        self._drop()
        self._sock.close()


class QnxFileSource:
    """The newest frame the ``qnxcam`` helper wrote to a file, read without a camera SDK.

    ``qnxcam`` replaces the file atomically (``rename``) and stamps each frame with the
    camera's own microsecond timestamp. This source polls every 20 ms and returns a frame
    only when that stamp changes, so a slow detector still sees every frame once and a
    still camera never repeats one. It blocks at most 0.5 s per call by default, which lets
    the detector send a heartbeat while the camera is quiet.
    """

    def __init__(self, path: str | Path, *, flip: bool = False, poll_s: float = QNX_POLL_S) -> None:
        if poll_s <= 0:
            raise ValueError("poll_s must be positive")
        self.path = Path(path)
        self.flip = flip
        self._poll_s = poll_s
        self._last_timestamp: int | None = None

    def read(self, wait_s: float | None = None) -> Frame | None:
        budget = QNX_WAIT_S if wait_s is None else max(0.0, wait_s)
        deadline = time.monotonic() + budget
        while True:
            frame = self._poll()
            if frame is not None:
                return time.monotonic(), frame
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            time.sleep(min(self._poll_s, left))

    def _poll(self) -> np.ndarray | None:
        try:
            with open(self.path, "rb") as fh:
                header = fh.read(QNX_HEADER_SIZE)
                if len(header) < QNX_HEADER_SIZE:
                    return None
                fields = QNX_HEADER.unpack(header)
                magic, _frametype, _height, _width, _stride, _reserved, _uv, timestamp, nbytes = fields
                if magic != QNX_MAGIC or nbytes > MAX_FRAME_BYTES:
                    return None
                if self._last_timestamp is not None and int(timestamp) == self._last_timestamp:
                    return None
                fh.seek(0)
                data = fh.read(QNX_HEADER_SIZE + int(nbytes))
        except OSError:
            return None
        if len(data) < QNX_HEADER_SIZE + int(nbytes):
            return None  # a half-written file; the next poll gets the whole one
        try:
            img = decode_qnx_frame(data, flip=self.flip)
        except ValueError:
            return None
        self._last_timestamp = int(timestamp)
        return img

    def restart(self) -> None:
        """Keep the newest frame: rewinding a live camera would only replay it."""

    def close(self) -> None:
        pass


def decode_qnx_frame(data: bytes, *, flip: bool = False) -> np.ndarray:
    """Decode one ``qnxcam`` file image (header plus pixels) into BGR.

    Raises ValueError for a truncated file, an unknown frametype or an implausible size,
    so a corrupt or hostile file cannot make OpenCV allocate an enormous bitmap.
    """
    if len(data) < QNX_HEADER_SIZE:
        raise ValueError("qnxcam file is shorter than its header")
    magic, frametype, height, width, stride, _reserved, uv_offset, _timestamp, nbytes = (
        QNX_HEADER.unpack_from(data, 0)
    )
    if magic != QNX_MAGIC:
        raise ValueError("not a qnxcam file (bad magic)")
    if width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValueError(f"invalid qnxcam dimensions {width}x{height}")
    if nbytes > MAX_FRAME_BYTES:
        raise ValueError(f"qnxcam frame of {nbytes} bytes exceeds {MAX_FRAME_BYTES}")
    body = data[QNX_HEADER_SIZE : QNX_HEADER_SIZE + int(nbytes)]
    if len(body) != nbytes:
        raise ValueError("truncated qnxcam frame")
    img = _qnx_to_bgr(frametype, width, height, stride, uv_offset, body)
    if flip:
        import cv2

        img = cv2.rotate(img, cv2.ROTATE_180)
    return img


def _qnx_to_bgr(
    frametype: int, width: int, height: int, stride: int, uv_offset: int, body: bytes
) -> np.ndarray:
    import cv2

    raw = np.frombuffer(body, dtype=np.uint8)
    bpp = _QNX_BYTES_PER_PIXEL.get(frametype)
    if frametype == FRAMETYPE_NV12:
        if stride < width:
            raise ValueError(f"NV12 stride {stride} is narrower than {width}")
        if uv_offset < 0 or uv_offset + (height // 2) * stride > len(raw):
            raise ValueError("NV12 UV plane is outside the frame")
        y = raw[: height * stride].reshape(height, stride)[:, :width]
        uv = raw[uv_offset : uv_offset + (height // 2) * stride].reshape(height // 2, stride)[:, :width]
        return cv2.cvtColor(np.vstack([y, uv]), cv2.COLOR_YUV2BGR_NV12)
    if bpp is None:
        raise ValueError(f"unhandled qnxcam frametype {frametype}")
    if stride < width * bpp:
        raise ValueError(f"stride {stride} is narrower than {width} pixels")
    if height * stride > len(raw):
        raise ValueError("qnxcam frame is shorter than height x stride")
    rows = raw[: height * stride].reshape(height, stride)[:, : width * bpp]
    packed = rows.reshape(height, width, bpp)
    if frametype == FRAMETYPE_YUYV:
        return cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_YUYV)
    if frametype == FRAMETYPE_UYVY:
        return cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_UYVY)
    return cv2.cvtColor(packed, cv2.COLOR_BGRA2BGR)


@dataclass(frozen=True)
class SourceSpec:
    """A parsed ``--source`` argument: a kind plus its string target."""

    kind: SourceKind
    target: str
    flip: bool = False

    @property
    def path(self) -> str:
        if self.kind not in ("dir", "qnxfile"):
            raise ValueError(f"source is {self.kind}, not a path")
        return self.target

    @property
    def index(self) -> int:
        if self.kind != "camera":
            raise ValueError(f"source is {self.kind}, not a camera")
        return int(self.target)

    @property
    def port(self) -> int:
        if self.kind != "net":
            raise ValueError(f"source is {self.kind}, not a network source")
        return int(self.target)


def parse_source_spec(spec: str) -> SourceSpec:
    """Parse ``dir:``/``camera:``/``net:``/``qnxfile:`` prefixes; a bare path is a directory."""
    value = spec.strip()
    if not value:
        raise ValueError("source must not be empty")
    if value.startswith("qnxfile:"):
        path = value[8:]
        flip = False
        if path.endswith(":flip"):
            path = path[: -len(":flip")]
            flip = True
        if not path:
            raise ValueError("qnxfile: needs a path")
        return SourceSpec("qnxfile", path, flip)
    if value.startswith("dir:"):
        path = value[4:]
        if not path:
            raise ValueError("dir: needs a path")
        return SourceSpec("dir", path)
    if value.startswith("camera:"):
        raw = value[7:]
        if not raw.isdigit():
            raise ValueError(f"camera: needs a non-negative integer, got {raw!r}")
        return SourceSpec("camera", str(int(raw)))
    if value.startswith("net:"):
        raw = value[4:]
        if not raw.isdigit() or int(raw) > 65535:
            raise ValueError(f"net: needs a port 0-65535, got {raw!r}")
        return SourceSpec("net", str(int(raw)))
    return SourceSpec("dir", value)


def open_source(spec: str, *, net_bind: str | None = None) -> FrameSource:
    """Build the source named by a ``--source`` argument."""
    parsed = parse_source_spec(spec)
    if parsed.kind == "dir":
        return DirSource(parsed.path)
    if parsed.kind == "camera":
        return CameraSource(parsed.index)
    if parsed.kind == "qnxfile":
        return QnxFileSource(parsed.path, flip=parsed.flip)
    return NetSource(parsed.port, net_bind or DEFAULT_NET_BIND)


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Header-only ``(width, height)`` for a JPEG or PNG, or None if it cannot be read."""
    if data.startswith(_JPEG_SOI):
        return _jpeg_dimensions(data)
    if data.startswith(PNG_SIGNATURE):
        return _png_dimensions(data)
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG markers to the first SOF segment; never trust the payload length."""
    index = 2
    size = len(data)
    while index + 4 <= size:
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte
            index += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:  # no length field
            index += 2
            continue
        segment = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment < 2:
            return None
        if marker in _SOF_MARKERS:
            if index + 9 > size:
                return None
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + segment  # skip the segment's payload
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """IHDR is always the first chunk: signature, length, type, width, height."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def decode_frame(frame: FrameData) -> np.ndarray:
    """Return a BGR image, decoding JPEG/PNG bytes only after checking their dimensions."""
    if isinstance(frame, np.ndarray):
        return frame
    dimensions = image_dimensions(frame)
    if dimensions is None:
        raise ValueError("cannot read frame dimensions")
    width, height = dimensions
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid frame dimensions {width}x{height}")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValueError(
            f"frame dimensions {width}x{height} exceed {MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION}"
        )
    import cv2

    # Reduced decode halves each side: cheap, and the detector letterboxes to 416 anyway.
    img = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_REDUCED_COLOR_2)
    if img is None:
        raise ValueError("cannot decode frame")
    return img
