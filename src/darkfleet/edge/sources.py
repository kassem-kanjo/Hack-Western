"""Frame sources for the untrusted detector: a directory, a camera or the network.

Every source returns ``(t_capture, frame)`` where ``t_capture`` is a
``time.monotonic()`` value taken when the frame was read. A frame is either a
decoded BGR ``numpy`` image or the encoded JPEG bytes that arrived over the
network; :func:`decode_frame` imports OpenCV only when it actually has to decode
one, so this module still imports on a QNX board without OpenCV.

``--source`` accepts ``dir:<path>``, ``camera:<index>`` and ``net:<port>``. A
plain path means a directory, which keeps the original detector command line
working.
"""

from __future__ import annotations

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
_ACCEPT_TIMEOUT_S = 0.2
_RECV_TIMEOUT_S = 1.0
_RECV_CHUNK = 1 << 16

FrameData = np.ndarray | bytes
Frame = tuple[float, FrameData]
SourceKind = Literal["dir", "camera", "net"]


def list_images(root: Path) -> list[Path]:
    """Image files directly inside `root`, in deterministic name order."""
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


class FrameSource(Protocol):
    """What the detector needs: pull one frame, rewind, release."""

    def read(self) -> Frame | None:
        """Return the next frame, or None when none is available right now."""
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

    def read(self) -> Frame | None:
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

    def read(self) -> Frame | None:
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
    """

    def __init__(self, port: int, host: str = "0.0.0.0") -> None:
        if not 0 <= port <= 65535:
            raise ValueError(f"port out of range: {port}")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        self._sock.settimeout(_ACCEPT_TIMEOUT_S)
        self.port: int = self._sock.getsockname()[1]
        self._conn: socket.socket | None = None

    def read(self) -> Frame | None:
        """One complete JPEG frame. None means no client, a timeout or a dropped peer."""
        while True:
            if not self._accept():
                return None
            assert self._conn is not None
            try:
                header = self._recv_exact(FRAME_HEADER.size)
                if header is None:
                    if self._conn is None:
                        continue  # peer disconnected; a reconnect may already be queued
                    return None
                (length,) = FRAME_HEADER.unpack(header)
                if length == 0 or length > MAX_FRAME_BYTES:
                    self._drop()
                    continue
                payload = self._recv_exact(length)
                if payload is None:
                    if self._conn is None:
                        continue
                    return None
                return time.monotonic(), payload
            except OSError:
                self._drop()
                return None

    def _accept(self) -> bool:
        if self._conn is not None:
            return True
        try:
            conn, _ = self._sock.accept()
        except TimeoutError:
            return False
        except OSError:
            return False
        conn.settimeout(_RECV_TIMEOUT_S)
        self._conn = conn
        return True

    def _recv_exact(self, n: int) -> bytes | None:
        assert self._conn is not None
        chunks: list[bytes] = []
        remaining = n
        while remaining > 0:
            chunk = self._conn.recv(min(remaining, _RECV_CHUNK))
            if not chunk:
                self._drop()
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _drop(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def restart(self) -> None:
        """Keep the connection: reconnecting is the bridge's job, not the detector's."""

    def close(self) -> None:
        self._drop()
        self._sock.close()


@dataclass(frozen=True)
class SourceSpec:
    """A parsed ``--source`` argument: a kind plus its string target."""

    kind: SourceKind
    target: str

    @property
    def path(self) -> str:
        if self.kind != "dir":
            raise ValueError(f"source is {self.kind}, not a directory")
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
    """Parse ``dir:``/``camera:``/``net:`` prefixes; a bare path is a directory."""
    value = spec.strip()
    if not value:
        raise ValueError("source must not be empty")
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


def open_source(spec: str) -> FrameSource:
    """Build the source named by a ``--source`` argument."""
    parsed = parse_source_spec(spec)
    if parsed.kind == "dir":
        return DirSource(parsed.path)
    if parsed.kind == "camera":
        return CameraSource(parsed.index)
    return NetSource(parsed.port)


def decode_frame(frame: FrameData) -> np.ndarray:
    """Return a BGR image, decoding JPEG bytes only when the source sent bytes."""
    if isinstance(frame, np.ndarray):
        return frame
    import cv2

    img = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("cannot decode JPEG frame")
    return img
