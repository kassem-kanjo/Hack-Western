"""Length-prefixed JSON messages over a Unix domain socket.

Wire format: a 4-byte big-endian unsigned length, then a UTF-8 JSON object.
Used between the detector (untrusted, low priority) and the safety core
(trusted, high priority). Only POSIX socket calls are used, so the same code
runs on macOS and on QNX.

Timestamps (`t_capture`, `t_done`, heartbeat `t`) are `time.monotonic()` values.
On macOS and QNX, CLOCK_MONOTONIC is system-wide, so the two processes on the
same board can compare them. Alert `t` is wall-clock time, for the log.
"""

from __future__ import annotations

import json
import select
import socket
import struct
import time
from typing import Any

MAX_MSG_BYTES = 1 << 20  # 1 MiB; a frame of boxes is a few hundred bytes.
_HEADER = struct.Struct("!I")

DETECTIONS = "DETECTIONS"
HEARTBEAT = "HEARTBEAT"
ALERT = "ALERT"
MESSAGE_TYPES = frozenset({DETECTIONS, HEARTBEAT, ALERT})

# A heartbeat may say why no frame is coming. "waiting for frames" is the detector
# telling the supervisor it is alive but its source has not delivered anything yet.
WAITING_FOR_FRAMES = "waiting for frames"

_BOX_KEYS = ("x", "y", "w", "h")


class ProtocolError(ValueError):
    """A message is malformed or too large. Treat the peer as untrusted."""


# --- constructors -----------------------------------------------------------


def make_detections(
    frame_id: int,
    t_capture: float,
    t_done: float,
    boxes: list[dict[str, Any]],
    *,
    engine: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    msg = {
        "type": DETECTIONS,
        "frame_id": int(frame_id),
        "t_capture": float(t_capture),
        "t_done": float(t_done),
        "boxes": [_normalise_box(b) for b in boxes],
    }
    if engine is not None:
        msg["engine"] = str(engine)
    if source is not None:
        msg["source"] = str(source)
    validate(msg)
    return msg


def make_heartbeat(
    t: float | None = None,
    state: str | None = None,
    sched: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A liveness beat. ``state`` is advisory ("waiting for frames"); ``sched`` is the
    detector's real scheduling class, sent once so the status file can report it."""
    msg: dict[str, Any] = {"type": HEARTBEAT, "t": float(time.monotonic() if t is None else t)}
    if state is not None:
        msg["state"] = str(state)
    if sched is not None:
        msg["sched"] = dict(sched)
    validate(msg)
    return msg


def make_alert(
    kind: str, frame_id: int, confidence: float, box: dict[str, Any], t: float | None = None
) -> dict[str, Any]:
    msg = {
        "type": ALERT,
        "kind": str(kind),
        "frame_id": int(frame_id),
        "confidence": float(confidence),
        "box": _normalise_box(box),
        "t": float(time.time() if t is None else t),
    }
    validate(msg)
    return msg


# --- validation -------------------------------------------------------------


def validate(msg: Any) -> None:
    """Raise ProtocolError unless `msg` has a known type and the right fields."""
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    mtype = msg.get("type")
    if mtype not in MESSAGE_TYPES:
        raise ProtocolError(f"unknown message type: {mtype!r}")
    if mtype == DETECTIONS:
        if not _is_int(msg.get("frame_id")):
            raise ProtocolError("frame_id must be an int")
        for key in ("t_capture", "t_done"):
            _require_number(msg, key)
        boxes = msg.get("boxes")
        if not isinstance(boxes, list):
            raise ProtocolError("boxes must be a list")
        for box in boxes:
            if not isinstance(box, dict) or not isinstance(box.get("cls"), str):
                raise ProtocolError("each box needs a string cls")
            for key in ("score", *_BOX_KEYS):
                _require_number(box, key)
        for key in ("engine", "source"):
            if key in msg and not isinstance(msg[key], str):
                raise ProtocolError(f"{key} must be a string")
    elif mtype == HEARTBEAT:
        _require_number(msg, "t")
        if "state" in msg and not isinstance(msg["state"], str):
            raise ProtocolError("heartbeat state must be a string")
        if "sched" in msg:
            sched = msg["sched"]
            if not isinstance(sched, dict) or not isinstance(sched.get("policy"), str):
                raise ProtocolError("heartbeat sched needs a string policy")
            _require_number(sched, "priority")
            if "nice" in sched:
                _require_number(sched, "nice")
    elif mtype == ALERT:
        if not isinstance(msg.get("kind"), str):
            raise ProtocolError("alert kind must be a string")
        if not _is_int(msg.get("frame_id")):
            raise ProtocolError("alert frame_id must be an int")
        _require_number(msg, "confidence")
        _require_number(msg, "t")
        box = msg.get("box")
        if not isinstance(box, dict):
            raise ProtocolError("alert box must be an object")
        for key in _BOX_KEYS:
            _require_number(box, key)


def _is_int(value: Any) -> bool:
    # bool is a subclass of int in Python; a JSON `true` must not pass as a frame id.
    return isinstance(value, int) and not isinstance(value, bool)


def _require_number(obj: dict[str, Any], key: str) -> None:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{key} must be a number")


def _normalise_box(box: dict[str, Any]) -> dict[str, Any]:
    out = {
        "cls": str(box["cls"]),
        "score": float(box["score"]),
        "x": float(box["x"]),
        "y": float(box["y"]),
        "w": float(box["w"]),
        "h": float(box["h"]),
    }
    return out


# --- encoding ---------------------------------------------------------------


def encode(msg: dict[str, Any]) -> bytes:
    validate(msg)
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MSG_BYTES:
        raise ProtocolError(f"message of {len(body)} bytes exceeds {MAX_MSG_BYTES}")
    return _HEADER.pack(len(body)) + body


def _decode_body(body: bytes) -> dict[str, Any]:
    try:
        msg = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    validate(msg)
    return msg


# --- socket I/O -------------------------------------------------------------


def send_msg(sock: socket.socket, msg: dict[str, Any]) -> None:
    """Send one message. Blocks until the kernel accepts all bytes."""
    sock.sendall(encode(msg))


class MessageDecoder:
    """Incremental decoder for non-blocking readers (the safety core uses this).

    Feed whatever bytes `recv` returned; get back the complete, validated
    messages. Partial messages are kept until the rest arrives.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buf.extend(data)
        out: list[dict[str, Any]] = []
        while len(self._buf) >= _HEADER.size:
            (length,) = _HEADER.unpack_from(self._buf, 0)
            if length > MAX_MSG_BYTES:
                raise ProtocolError(f"declared length {length} exceeds {MAX_MSG_BYTES}")
            end = _HEADER.size + length
            if len(self._buf) < end:
                break
            body = bytes(self._buf[_HEADER.size : end])
            del self._buf[:end]
            out.append(_decode_body(body))
        return out


def recv_msg(sock: socket.socket, timeout: float | None = None) -> dict[str, Any] | None:
    """Blocking receive of exactly one message.

    Returns None on a clean EOF (peer closed before a new message). Raises
    TimeoutError if `timeout` seconds pass with no data, and ProtocolError on
    a malformed or truncated message. Reads exactly the bytes of one message,
    so back-to-back messages are not lost.
    """
    deadline = None if timeout is None else time.monotonic() + timeout  # one budget for header and body
    header = _recv_exact(sock, _HEADER.size, deadline, allow_eof=True)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_MSG_BYTES:
        raise ProtocolError(f"declared length {length} exceeds {MAX_MSG_BYTES}")
    body = _recv_exact(sock, length, deadline, allow_eof=False)
    assert body is not None
    return _decode_body(body)


def _recv_exact(sock: socket.socket, n: int, deadline: float | None, allow_eof: bool) -> bytes | None:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("timed out waiting for a message")
            readable, _, _ = select.select([sock], [], [], left)
            if not readable:
                raise TimeoutError("timed out waiting for a message")
        chunk = sock.recv(remaining)
        if not chunk:
            if allow_eof and remaining == n:
                return None
            raise ProtocolError("connection closed mid-message")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
