"""Read the newest frame qnxcam wrote and save it as a JPEG (probe for the detector source)."""

import struct
import sys

import cv2
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/qnxcam.frame"
out = sys.argv[2] if len(sys.argv) > 2 else "/data/home/qnxuser/qnxcam.jpg"
data = open(path, "rb").read()
magic, ft, h, w, stride, _ = struct.unpack_from("<6I", data, 0)
uv_off, ts, n = struct.unpack_from("<qqQ", data, 24)
raw = np.frombuffer(data, dtype=np.uint8, offset=48, count=n)
print("type", ft, "h", h, "w", w, "stride", stride, "uv_off", uv_off, "bytes", n)


def packed(bytes_per_px: int) -> np.ndarray:
    """Rows of `stride` bytes cropped to the visible width, as (h, w, bytes_per_px)."""
    rows = raw[: h * stride].reshape(h, stride)[:, : w * bytes_per_px]
    return rows.reshape(h, w, bytes_per_px)


if ft == 14:  # YCbYCr = YUYV
    img = cv2.cvtColor(packed(2), cv2.COLOR_YUV2BGR_YUYV)
elif ft == 8:  # CbYCrY = UYVY
    img = cv2.cvtColor(packed(2), cv2.COLOR_YUV2BGR_UYVY)
elif ft == 1:  # NV12
    y = raw[: h * stride].reshape(h, stride)[:, :w]
    uv = raw[uv_off : uv_off + (h // 2) * stride].reshape(h // 2, stride)[:, :w]
    img = cv2.cvtColor(np.vstack([y, uv]), cv2.COLOR_YUV2BGR_NV12)
elif ft == 2:  # RGB8888
    img = cv2.cvtColor(packed(4), cv2.COLOR_BGRA2BGR)
else:
    sys.exit(f"unhandled frametype {ft}")
cv2.imwrite(out, img)
print("saved", out, img.shape)
