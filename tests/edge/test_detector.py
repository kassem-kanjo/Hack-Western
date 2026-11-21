"""Tests for the untrusted detector process (no camera, model or network)."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from darkfleet.edge import detector
from darkfleet.edge.protocol import DETECTIONS, HEARTBEAT, WAITING_FOR_FRAMES, recv_msg

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = "tests/edge/fixtures"


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_decode_yolox_maps_box_back_to_source() -> None:
    stride, side, row, col = 8, 52, 10, 20
    index = row * side + col
    raw = np.zeros((3549, 85), dtype=np.float32)
    raw[index, 0:2] = 0.5
    raw[index, 2:4] = math.log(4.0)
    raw[index, 4] = 0.9
    raw[index, 5 + 8] = 0.9  # class 8 "boat" lives after the 5 box/object columns
    ratio = 0.5

    boxes = detector.decode_yolox(raw, ratio, score_min=0.3)

    cx = (col + 0.5) * stride
    cy = (row + 0.5) * stride
    side_px = math.exp(math.log(4.0)) * stride
    assert len(boxes) == 1
    box = boxes[0]
    assert box["cls"] == "boat"
    assert box["score"] == pytest.approx(0.9 * 0.9)
    assert box["x"] == pytest.approx((cx - side_px / 2) / ratio)
    assert box["y"] == pytest.approx((cy - side_px / 2) / ratio)
    assert box["w"] == pytest.approx(side_px / ratio)
    assert box["h"] == pytest.approx(side_px / ratio)
    # ratio 0.5 means letterbox pixels are halved on the way back.
    assert box["w"] == pytest.approx(2 * side_px)


def test_decode_drops_low_scores_and_non_boats() -> None:
    index = 10 * 52 + 20
    raw = np.zeros((3549, 85), dtype=np.float32)
    raw[index, 0:2] = 0.5
    raw[index, 2:4] = math.log(4.0)
    raw[index, 4] = 0.9
    raw[index, 5 + 0] = 0.99  # class 0 "person", high score
    raw[index, 5 + 8] = 0.2  # boat score 0.9 * 0.2 = 0.18

    assert detector.decode_yolox(raw, 1.0, score_min=0.3) == []


def test_nms_suppresses_overlap() -> None:
    a = {"cls": "boat", "score": 0.9, "x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0}
    b = {"cls": "boat", "score": 0.8, "x": 1.0, "y": 1.0, "w": 10.0, "h": 10.0}
    assert detector.iou(a, b) > 0.45

    kept = detector.nms([a, b], detector.NMS_IOU)

    assert kept == [a]


def test_mock_frames_are_deterministic() -> None:
    first = detector.mock_boxes(0)
    boats = [b for b in first if b["cls"] == "boat"]
    assert len(boats) == 1
    assert boats[0]["score"] == pytest.approx(0.90)
    assert any(b["cls"] == "person" for b in first)

    second = detector.mock_boxes(1)
    assert [b["score"] for b in second if b["cls"] == "boat"] == pytest.approx([0.89])

    assert detector.mock_boxes(2) == []
    assert detector.mock_boxes(0) == first


def test_mock_end_to_end_over_unix_socket() -> None:
    tmpdir = tempfile.mkdtemp(dir="/tmp")
    sock_path = os.path.join(tmpdir, "d.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    listener.listen(1)
    listener.settimeout(10)
    proc = None
    conn = None
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "darkfleet.edge.detector",
                "--source",
                FIXTURES,
                "--socket",
                sock_path,
                "--mock",
                "--fps",
                "50",
                "--once",
            ],
            cwd=ROOT,
            env=_subprocess_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        conn, _ = listener.accept()

        messages = []
        while len(messages) < 6:
            try:
                msg = recv_msg(conn, timeout=5)
            except TimeoutError:
                break
            if msg is None:
                break
            messages.append(msg)

        assert messages
        assert messages[0]["type"] == DETECTIONS
        assert messages[0]["frame_id"] == 0
        assert any(box["cls"] == "boat" for box in messages[0]["boxes"])
        frame_ids = [m["frame_id"] for m in messages if m["type"] == DETECTIONS]
        assert frame_ids == sorted(frame_ids)
        assert len(set(frame_ids)) == len(frame_ids)
        return_code = proc.wait(timeout=10)
        assert return_code == 0, proc.stderr.read().decode()
    finally:
        if conn is not None:
            conn.close()
        listener.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_frame_writer_writes_latest_jpg(tmp_path: Path) -> None:
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((480, 1280, 3), dtype=np.uint8)
    boxes = [{"cls": "boat", "score": 0.87, "x": 100.0, "y": 80.0, "w": 80.0, "h": 40.0}]

    writer = detector.FrameWriter(str(tmp_path))
    assert writer.enabled
    writer.write(frame, boxes)

    path = tmp_path / "latest.jpg"
    assert path.is_file()
    assert not (tmp_path / "latest.jpg.tmp").exists()
    img = cv2.imread(str(path))
    assert img is not None
    assert img.shape[1] == detector.MAX_FRAME_WIDTH  # a 1280 px source is scaled down to 640
    assert path.stat().st_size > 0


def test_missing_model_exits_3() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "darkfleet.edge.detector",
            "--source",
            FIXTURES,
            "--socket",
            "/tmp/df-missing-model.sock",
            "--model",
            "/nonexistent/df-yolox.onnx",
        ],
        cwd=ROOT,
        env=_subprocess_env(),
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 3
    assert b"fetch_edge_model.sh" in result.stderr
    assert b"--mock" in result.stderr


def test_detector_heartbeats_while_waiting_for_frames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No frame ever arrives (a net source with no bridge): the detector must still be
    alive, heartbeating at least twice a second with state "waiting for frames"."""
    monkeypatch.setattr(
        detector,
        "OnnxDetector",
        lambda *_a: type("Model", (), {"detect": lambda *_: [], "warm_up": lambda *_: None})(),
    )
    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"not really a model, never loaded")

    sock_dir = tempfile.mkdtemp(prefix="dfw-", dir="/tmp")
    sock_path = os.path.join(sock_dir, "w.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    listener.listen(1)
    listener.settimeout(10)
    conn = None
    thread = None
    try:
        args = argparse.Namespace(
            source="net:0",
            net_bind="127.0.0.1",
            socket=sock_path,
            state_dir=str(tmp_path),
            mock=False,
            model=str(model_file),
            fps=10.0,
            heartbeat_s=0.1,
            once=False,
            nice=0,
            score_min=0.3,
            input_size=416,
            standby=False,
        )
        thread = threading.Thread(target=detector.run, args=(args,), daemon=True)
        thread.start()
        conn, _ = listener.accept()

        messages: list[tuple[float, dict]] = []
        end = time.monotonic() + 2.5
        while time.monotonic() < end:
            try:
                msg = recv_msg(conn, timeout=max(0.05, end - time.monotonic()))
            except TimeoutError:
                break
            if msg is None:
                break
            messages.append((time.monotonic(), msg))

        assert len(messages) >= 3, "the detector went silent while the source was quiet"
        heartbeats = [msg for _, msg in messages if msg["type"] == HEARTBEAT]
        assert len(heartbeats) >= 3
        assert all(hb.get("state") == WAITING_FOR_FRAMES for hb in heartbeats)
        assert heartbeats[0]["sched"]["nice"] == 0  # args.nice is 0 in this test
        gaps = [later - earlier for (earlier, _), (later, _) in zip(messages, messages[1:], strict=False)]
        assert max(gaps) < 1.0, f"heartbeat gap {max(gaps):.2f}s is far beyond the wait budget"
    finally:
        if conn is not None:
            conn.close()
        listener.close()
        if thread is not None:
            thread.join(timeout=5)
        shutil.rmtree(sock_dir, ignore_errors=True)
