"""Untrusted, low-priority boat detector process.

Reads frames from a directory, a camera or the network bridge, runs a
YOLOX-nano ONNX "boat" detector, and sends DETECTIONS/HEARTBEAT messages to the
safety core over a Unix socket. `--mock` needs no camera, model, cv2 or
onnxruntime.

`--source` is `dir:<path>`, `camera:<index>` or `net:<port>`; a plain path is
still read as a directory.
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
from darkfleet.edge.sources import (
    FrameSource,
    decode_frame,
    list_images,
    open_source,
    parse_source_spec,
)

DEFAULT_MODEL = "data/models/yolox_nano.onnx"
INPUT_SIZE = 416
PAD_VALUE = 114
BOAT_CLASS = 8  # COCO "boat"
STRIDES = (8, 16, 32)
NMS_IOU = 0.45
CONNECT_TIMEOUT_S, CONNECT_RETRY_S, SLEEP_SLICE_S = 10.0, 0.05, 0.05
MOCK_SYNTHETIC_FRAMES = 12

# Annotated frames for the console. At most one every ANNOTATE_EVERY frames, plus any frame
# that produced a detection, so the demo always shows boxes rather than a stale still.
LATEST_FRAME_NAME = "latest.jpg"
MAX_FRAME_WIDTH = 640
JPEG_QUALITY = 80
ANNOTATE_EVERY = 2
_DEFAULT_FRAME_HW = (360, 640, 3)

Box = dict[str, Any]


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
    spec = parse_source_spec(source)
    root = Path(spec.path) if spec.kind == "dir" else None
    paths = list_images(root) if root is not None and root.is_dir() else []
    if paths:
        return [(p.name, mock_boxes(i)) for i, p in enumerate(paths)]
    return [(f"mock-{i:06d}", mock_boxes(i)) for i in range(MOCK_SYNTHETIC_FRAMES)]


class OnnxDetector:
    """YOLOX-nano on onnxruntime, or on OpenCV's DNN engine when onnxruntime can't load.

    The fallback exists because QNX's python3-onnxruntime 1.24.4-r0 (repo.oss.qnx.com, 8.0.x)
    ships without its compiled pybind module, so `import onnxruntime` fails on the Pi; OpenCV
    4.12 from the same repository runs the same ONNX graph. `engine` names the one in use.
    """

    def __init__(self, model_path: str) -> None:
        self._session = None
        self._net = None
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
            self._input_name = self._session.get_inputs()[0].name
            self.engine = f"onnxruntime {ort.__version__}"
        except ImportError:
            import cv2

            self._net = cv2.dnn.readNetFromONNX(str(model_path))
            self.engine = f"opencv-dnn {cv2.__version__}"
        print(f"detector: inference engine {self.engine}", file=sys.stderr)

    def _run(self, blob: np.ndarray) -> np.ndarray:
        if self._session is not None:
            return self._session.run(None, {self._input_name: blob})[0]
        self._net.setInput(blob)
        return self._net.forward()

    def detect(self, img: np.ndarray, score_min: float) -> list[Box]:
        import cv2

        h, w = img.shape[:2]
        ratio = letterbox((h, w), INPUT_SIZE)
        new_w, new_h = max(1, round(w * ratio)), max(1, round(h * ratio))
        canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), PAD_VALUE, dtype=np.uint8)
        canvas[:new_h, :new_w] = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)
        output = self._run(blob)
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


class FrameWriter:
    """Keeps ``<state-dir>/latest.jpg`` current: boxes drawn, width <= 640, written atomically.

    cv2 is optional. When it cannot be imported (a ``--mock`` run on a board without OpenCV)
    the writer is simply disabled and nothing is written. Drawing and encoding happen after
    the DETECTIONS message is sent, so a slow disk cannot inflate `last_inference_ms`.
    """

    def __init__(self, state_dir: str) -> None:
        self.dir = Path(state_dir)
        self.path = self.dir / LATEST_FRAME_NAME
        try:
            import cv2
        except ImportError:  # no OpenCV: annotated frames are not available on this unit
            self._cv2: Any = None
        else:
            self._cv2 = cv2

    @property
    def enabled(self) -> bool:
        return self._cv2 is not None

    def blank(self) -> np.ndarray:
        """A dark synthetic frame for ``--mock``, where the source supplies no pixels."""
        assert self._cv2 is not None
        return np.full(_DEFAULT_FRAME_HW, 24, dtype=np.uint8)

    def annotate(self, img: np.ndarray, boxes: list[Box]) -> np.ndarray:
        cv2 = self._cv2
        assert cv2 is not None
        out = img.copy()
        for box in boxes:
            x, y = int(round(box["x"])), int(round(box["y"]))
            w, h = int(round(box["w"])), int(round(box["h"]))
            colour = (0, 220, 0) if box.get("cls") == "boat" else (0, 170, 255)
            cv2.rectangle(out, (x, y), (x + w, y + h), colour, 2)
            label = f"{box.get('cls', 'object')} {float(box.get('score', 0.0)):.2f}"
            ty = y - 6 if y > 14 else y + h + 16
            cv2.putText(out, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
        return self._fit_width(out)

    def _fit_width(self, img: np.ndarray) -> np.ndarray:
        cv2 = self._cv2
        assert cv2 is not None
        h, w = img.shape[:2]
        if w <= MAX_FRAME_WIDTH:
            return img
        new_h = max(1, round(h * MAX_FRAME_WIDTH / w))
        return cv2.resize(img, (MAX_FRAME_WIDTH, new_h), interpolation=cv2.INTER_AREA)

    def write(self, img: np.ndarray, boxes: list[Box]) -> None:
        """Annotate and atomically replace ``latest.jpg``; never raise into the frame loop."""
        cv2 = self._cv2
        if cv2 is None:
            return
        ok, encoded = cv2.imencode(
            ".jpg", self.annotate(img, boxes), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ok:
            return
        tmp = str(self.path) + ".tmp"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.write(encoded.tobytes())
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def benchmark(self) -> None:
        """Time one annotated write once at start so the per-frame cost is visible in the log."""
        if self._cv2 is None:
            return
        boxes: list[Box] = [{"cls": "boat", "score": 0.90, "x": 100.0, "y": 80.0, "w": 80.0, "h": 40.0}]
        start = time.perf_counter()
        self.write(self.blank(), boxes)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        print(
            f"detector: annotated frames -> {self.path} ({elapsed_ms:.1f} ms/frame)",
            file=sys.stderr,
        )


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
    source: FrameSource | None = None if args.mock else open_source(args.source)
    model = None if args.mock else OnnxDetector(args.model)

    state_dir = args.state_dir or os.path.dirname(os.path.abspath(args.socket))
    writer = FrameWriter(state_dir)
    if writer.enabled:
        writer.benchmark()
    base_frame = writer.blank() if writer.enabled else None

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
            img: np.ndarray | None = None
            if frames is not None:
                t_capture = time.monotonic()
                if index >= len(frames):
                    if args.once:
                        return 0
                    index = 0
                boxes = frames[index][1]
                img = base_frame
            else:
                assert source is not None and model is not None
                capture = source.read()
                if capture is None:
                    if args.once:
                        return 0
                    source.restart()
                    link.pause(max(interval, SLEEP_SLICE_S))
                    continue
                t_capture, frame = capture
                img = decode_frame(frame)
                boxes = model.detect(img, args.score_min)
            t_done = time.monotonic()
            link.send(make_detections(frame_id, t_capture, t_done, boxes))
            if img is not None and (frame_id % ANNOTATE_EVERY == 0 or boxes):
                writer.write(img, boxes)
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
        if source is not None:
            source.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.detector")
    parser.add_argument(
        "--source",
        required=True,
        help="dir:<path>, camera:<index> or net:<port> (a plain path means a directory)",
    )
    parser.add_argument("--socket", required=True, help="safety-core Unix socket")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="where latest.jpg is written (default: the socket's directory)",
    )
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
