#!/usr/bin/env python3
"""Rewrite the YOLOX-nano ONNX input from 416 to a smaller square size.

The network is fully convolutional and the safety code decodes the raw grid itself, so a static
shape edit is enough. 320 px cuts OpenCV DNN inference on the Pi 5 from ~180 ms to ~100 ms.

    uv run --extra edge --with onnx python scripts/resize_edge_model.py \\
        data/models/yolox_nano.onnx data/models/yolox_nano_320.onnx 320
"""

import sys

import numpy as np
import onnx
import onnxruntime as ort


def main(src: str, dst: str, size: int) -> None:
    model = onnx.load(src)
    for inp in model.graph.input:
        dim = inp.type.tensor_type.shape.dim
        dim[2].dim_value = size
        dim[3].dim_value = size
    del model.graph.value_info[:]
    for out in model.graph.output:
        dim = out.type.tensor_type.shape.dim
        dim[1].ClearField("dim_value")
        dim[1].dim_param = "n"
    onnx.save(model, dst)
    session = ort.InferenceSession(dst, providers=["CPUExecutionProvider"])
    out = session.run(None, {"images": np.zeros((1, 3, size, size), np.float32)})[0]
    expected = sum((size // stride) ** 2 for stride in (8, 16, 32))
    assert out.shape == (1, expected, 85), out.shape
    print(f"{dst}: input {size}px, {expected} anchors")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
