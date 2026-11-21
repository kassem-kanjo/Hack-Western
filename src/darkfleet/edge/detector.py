"""Untrusted, low-priority boat detector process.

Reads frames from an image directory, a video file or a camera index, runs a
YOLOX-nano ONNX "boat" detector, and sends DETECTIONS/HEARTBEAT messages to the
safety core over a Unix socket. `--mock` needs no camera, model, cv2 or
onnxruntime.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from darkfleet.edge.protocol import make_detections, make_heartbeat, send_msg

DEFAULT_MODEL = "data/models/yolox_nano.onnx"
INPUT_SIZE = 416
PAD_VALUE = 114
BOAT_CLASS = 8  # COCO "boat"
STRIDES = (8, 16, 32)
NMS_IOU = 0.45
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp"})
CONNECT_TIMEOUT_S, CONNECT_RETRY_S, SLEEP_SLICE_S = 10.0, 0.05, 0.05
MOCK_SYNTHETIC_FRAMES = 12

Box = dict[str, Any]


def _images(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def letterbox(img_hw: tuple[int, int], size: int = INPUT_SIZE) -> float:
    """Scale ratio that fits an image of shape (h, w) inside size x size."""
    h, w = img_hw
    if h <= 0 or w <= 0:
        raise ValueError(f"invalid image size: {img_hw!r}")
    return min(size / h, size / w)


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two top-left x,y,w,h boxes."""
    iw = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    ih = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    inter = max(0.0, iw) * max(0.0, ih)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: list[Box], iou_thr: float = NMS_IOU) -> list[Box]:
    """Greedy, score-ordered non-maximum suppression."""
    kept: list[Box] = []
    for box in sorted(boxes, key=lambda b: b["score"], reverse=True):
        if all(iou(box, other) <= iou_thr for other in kept):
            kept.append(box)
    return kept


def decode_yolox(
    raw: np.ndarray, ratio: float, score_min: float, nms_iou: float = NMS_IOU, input_size: int = INPUT_SIZE
) -> list[Box]:
    """Decode raw YOLOX output (N, 85) into boat boxes in source pixels."""
    if ratio <= 0:
        raise ValueError("ratio must be positive")
    raw = np.asarray(raw, dtype=np.float32)
    expected = sum((input_size // s) ** 2 for s in STRIDES)
    if raw.ndim != 2 or raw.shape[1] < 5 + BOAT_CLASS + 1 or raw.shape[0] != expected:
        raise ValueError(f"expected raw shape ({expected}, 85), got {raw.shape}")

    boxes: list[Box] = []
    offset = 0
    for stride in STRIDES:
        side = input_size // stride
        chunk = raw[offset : offset + side * side]
        offset += side * side
        grid = np.stack(np.meshgrid(np.arange(side), np.arange(side), indexing="xy"), axis=-1)
        xy = (chunk[:, :2] + grid.reshape(-1, 2)) * stride
        wh = np.exp(chunk[:, 2:4]) * stride
        boat_scores = chunk[:, 4] * chunk[:, 5 + BOAT_CLASS]
        for i in np.nonzero(boat_scores >= score_min)[0]:
            cx, cy = float(xy[i, 0]), float(xy[i, 1])
            bw, bh = float(wh[i, 0]), float(wh[i, 1])
            x, y = (cx - bw / 2) / ratio, (cy - bh / 2) / ratio
            w, h = bw / ratio, bh / ratio
            boxes.append({"cls": "boat", "score": float(boat_scores[i]), "x": x, "y": y, "w": w, "h": h})
    return nms(boxes, nms_iou)


def mock_boxes(frame_index: int) -> list[Box]:
    if frame_index % 3 == 2:
        return []
    score = 0.90 - 0.01 * (frame_index % 5)
    return [
        {"cls": "boat", "score": score, "x": 100.0, "y": 120.0, "w": 80.0, "h": 40.0},
        {"cls": "person", "score": 0.80, "x": 300.0, "y": 150.0, "w": 60.0, "h": 120.0},
    ]


def mock_frames(source: str) -> list[tuple[str, list[Box]]]:
    root = Path(source)
    paths = _images(root) if root.is_dir() else []
    if paths:
        return [(p.name, mock_boxes(i)) for i, p in enumerate(paths)]
    return [(f"mock-{i:06d}", mock_boxes(i)) for i in range(MOCK_SYNTHETIC_FRAMES)]


class RealSource:
    def __init__(self, source: str) -> None:
        import cv2

        self._cv2, self._next, self._paths = cv2, 0, None
        path = Path(source)
        self._paths = _images(path) if path.is_dir() else None
        if self._paths is not None and not self._paths:
            raise FileNotFoundError(f"no images in {source}")
        target = int(source) if source.lstrip("-").isdigit() else str(path)
        self._cap = None if self._paths is not None else cv2.VideoCapture(target)
        if self._cap is not None and not self._cap.isOpened():
            raise OSError(f"cannot open source: {source}")

    def read(self) -> np.ndarray | None:
        if self._paths is None:
            ok, img = self._cap.read()
            return img if ok else None
        if self._next >= len(self._paths):
            return None
        self._next += 1
        return self._cv2.imread(str(self._paths[self._next - 1]))

    def restart(self) -> None:
        if self._paths is not None:
            self._next = 0
        elif self._cap is not None:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, 0)


class OnnxDetector:
    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    def detect(self, img: np.ndarray, score_min: float) -> list[Box]:
        import cv2

        h, w = img.shape[:2]
        ratio = letterbox((h, w), INPUT_SIZE)
        new_w, new_h = max(1, round(w * ratio)), max(1, round(h * ratio))
        canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), PAD_VALUE, dtype=np.uint8)
        canvas[:new_h, :new_w] = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)
        output = self._session.run(None, {self._input_name: blob})[0]
        return decode_yolox(np.asarray(output)[0], ratio, score_min)


class Link:
    """Sends messages and keeps heartbeats flowing through paced sleeps."""

    def __init__(self, sock: socket.socket, heartbeat_s: float) -> None:
        self._sock, self._heartbeat_s = sock, heartbeat_s
        self._last_sent = time.monotonic()

    def send(self, msg: dict[str, Any]) -> None:
        send_msg(self._sock, msg)
        self._last_sent = time.monotonic()

    def pause(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            if time.monotonic() - self._last_sent >= self._heartbeat_s:
                self.send(make_heartbeat())
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, SLEEP_SLICE_S))


def run(args: argparse.Namespace) -> int:
    level = "default"
    try:
        if args.nice > 0:
            os.nice(args.nice)
            level = f"nice+{args.nice}"
    except OSError:
        pass
    print(f"detector priority: {level}", file=sys.stderr)

    if not args.mock and not Path(args.model).is_file():
        print(
            f"detector: model not found: {args.model}\n"
            "run scripts/fetch_edge_model.sh, or pass --mock for a model-free run",
            file=sys.stderr,
        )
        return 3

    frames = mock_frames(args.source) if args.mock else None
    source = None if args.mock else RealSource(args.source)
    model = None if args.mock else OnnxDetector(args.model)

    deadline = time.monotonic() + CONNECT_TIMEOUT_S
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(args.socket)
            break
        except OSError:
            sock.close()
            if time.monotonic() >= deadline:
                print(f"detector: cannot connect to {args.socket}", file=sys.stderr)
                return 2
            time.sleep(CONNECT_RETRY_S)

    link = Link(sock, args.heartbeat_s)
    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    frame_id = 0
    index = 0
    try:
        while True:
            t_capture = time.monotonic()
            if frames is not None:
                if index >= len(frames):
                    if args.once:
                        return 0
                    index = 0
                boxes = frames[index][1]
            else:
                assert source is not None and model is not None
                img = source.read()
                if img is None:
                    if args.once:
                        return 0
                    source.restart()
                    link.pause(max(interval, SLEEP_SLICE_S))
                    continue
                boxes = model.detect(img, args.score_min)
            t_done = time.monotonic()
            link.send(make_detections(frame_id, t_capture, t_done, boxes))
            frame_id += 1
            index += 1
            if frames is not None and index >= len(frames) and args.once:
                return 0
            link.pause(interval)
    except OSError as exc:
        print(f"detector: send failed: {exc}", file=sys.stderr)
        return 1
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.detector")
    parser.add_argument("--source", required=True, help="image directory, video file or camera index")
    parser.add_argument("--socket", required=True, help="safety-core Unix socket")
    parser.add_argument("--mock", action="store_true", help="no camera, no model, no cv2/onnxruntime")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--heartbeat-s", type=float, default=0.1)
    parser.add_argument("--once", action="store_true", help="exit 0 when the source is exhausted")
    parser.add_argument("--nice", type=int, default=10)
    parser.add_argument("--score-min", type=float, default=0.3)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        return run(args)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f"detector: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
