"""Tests for the edge wire protocol: length-prefixed JSON over a socket.

No camera and no network: everything runs over `socket.socketpair()` or the
in-memory decoder. See src/darkfleet/edge/protocol.py.
"""

import json
import socket
import struct
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from darkfleet.edge.protocol import (
    ALERT,
    DETECTIONS,
    HEARTBEAT,
    MAX_MSG_BYTES,
    MessageDecoder,
    ProtocolError,
    encode,
    make_alert,
    make_detections,
    make_heartbeat,
    recv_msg,
    send_msg,
    validate,
)

BOX = {"cls": "vessel", "score": 0.9, "x": 1.0, "y": 2.0, "w": 30.0, "h": 40.0}


def frame(body: bytes) -> bytes:
    """A wire frame with a correct length header around arbitrary body bytes."""
    return struct.pack("!I", len(body)) + body


@contextmanager
def socket_pair() -> Iterator[tuple[socket.socket, socket.socket]]:
    """Two connected sockets; both are closed on exit."""
    first, second = socket.socketpair()
    try:
        yield first, second
    finally:
        first.close()
        second.close()


# --- round trips ------------------------------------------------------------


def test_round_trip_detections_over_socketpair() -> None:
    msg = make_detections(7, 100.0, 100.25, [BOX, {**BOX, "cls": "buoy", "x": 50.0}])
    with socket_pair() as (sender, receiver):
        send_msg(sender, msg)
        assert recv_msg(receiver) == msg


def test_round_trip_heartbeat_and_alert() -> None:
    heartbeat = make_heartbeat(t=42.5)
    alert = make_alert("dark_vessel", 3, 0.87, BOX, t=1_700_000_000.0)
    assert heartbeat["type"] == HEARTBEAT
    assert alert["type"] == ALERT
    with socket_pair() as (sender, receiver):
        send_msg(sender, heartbeat)
        send_msg(sender, alert)
        assert recv_msg(receiver) == heartbeat
        assert recv_msg(receiver) == alert


# --- decoder ----------------------------------------------------------------


def test_decoder_handles_partial_and_multiple_messages() -> None:
    messages = [
        make_detections(1, 10.0, 10.5, [BOX]),
        make_heartbeat(t=11.0),
        make_alert("dark_vessel", 2, 0.5, BOX, t=12.0),
    ]
    stream = b"".join(encode(m) for m in messages)

    decoder = MessageDecoder()
    out: list[dict] = []
    for i in range(len(stream)):
        out.extend(decoder.feed(stream[i : i + 1]))
    assert out == messages

    decoder = MessageDecoder()
    assert decoder.feed(encode(messages[0]) + encode(messages[1])) == messages[:2]


def test_recv_msg_returns_none_on_clean_eof() -> None:
    with socket_pair() as (receiver, peer):
        peer.close()
        assert recv_msg(receiver) is None


def test_recv_msg_timeout() -> None:
    with socket_pair() as (receiver, _sender):
        with pytest.raises(TimeoutError):
            recv_msg(receiver, timeout=0.05)


def test_back_to_back_messages_not_lost_by_recv_msg() -> None:
    first = make_heartbeat(t=1.0)
    second = make_heartbeat(t=2.0)
    with socket_pair() as (sender, receiver):
        send_msg(sender, first)
        send_msg(sender, second)
        assert recv_msg(receiver) == first
        assert recv_msg(receiver) == second


# --- rejection --------------------------------------------------------------


def test_rejects_unknown_type() -> None:
    msg = {"type": "TELEMETRY", "t": 1.0}
    with pytest.raises(ProtocolError):
        validate(msg)
    with pytest.raises(ProtocolError):
        MessageDecoder().feed(frame(json.dumps(msg).encode("utf-8")))


@pytest.mark.parametrize(
    "broken",
    [
        {"type": DETECTIONS, "frame_id": 1, "t_capture": 1.0, "t_done": 2.0},
        {"type": DETECTIONS, "frame_id": True, "t_capture": 1.0, "t_done": 2.0, "boxes": []},
        {
            "type": DETECTIONS,
            "frame_id": 1,
            "t_capture": 1.0,
            "t_done": 2.0,
            "boxes": [{**BOX, "score": "high"}],
        },
    ],
    ids=["missing-boxes", "boolean-frame-id", "string-score"],
)
def test_rejects_bad_detection_fields(broken: dict) -> None:
    with pytest.raises(ProtocolError):
        validate(broken)
    with pytest.raises(ProtocolError):
        MessageDecoder().feed(frame(json.dumps(broken).encode("utf-8")))


def test_rejects_oversize_declared_length() -> None:
    header = struct.pack("!I", MAX_MSG_BYTES + 1)
    decoder = MessageDecoder()
    # Only the header is fed: the error must not wait for the (missing) body.
    with pytest.raises(ProtocolError):
        decoder.feed(header)


def test_rejects_invalid_json() -> None:
    with pytest.raises(ProtocolError):
        MessageDecoder().feed(frame(b"not json at all"))


def test_encode_rejects_invalid_message() -> None:
    # Sanity: encode validates before packing, so a bad message never hits the wire.
    with pytest.raises(ProtocolError):
        encode({"type": ALERT, "kind": "dark_vessel", "frame_id": 1, "confidence": 0.9})
