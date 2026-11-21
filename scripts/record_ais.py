"""Record the aisstream.io live feed for a region to hourly gzip JSONL files.

Why: Global Fishing Watch only publishes AIS gaps > 50 nm offshore and its per-vessel
presence API is down, so we build our own track history from the live feed and detect
silences ourselves. Runs unattended for hours: reconnects with backoff, never exits on
a network error, writes one file per UTC hour so a crash loses at most one partial hour.

Usage: uv run python scripts/record_ais.py --region BALTIC_WATCH --out data/cache/ais
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import websockets
from dotenv import dotenv_values

REGIONS = {
    # [[lat_min, lon_min], [lat_max, lon_max]] as aisstream expects
    "BALTIC_WATCH": [[53.5, 9.5], [66.0, 30.5]],
    "HORMUZ": [[22.0, 54.0], [27.5, 60.5]],
}
URL = "wss://stream.aisstream.io/v0/stream"


def hour_path(out: Path, region: str) -> Path:
    now = datetime.now(UTC)
    return out / region / f"{now:%Y%m%dT%H}.jsonl.gz"


async def record(region: str, out: Path, key: str) -> None:
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(URL, ping_interval=20, max_size=2**22) as ws:
                await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": [REGIONS[region]]}))
                backoff = 1.0
                path = hour_path(out, region)
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = gzip.open(path, "at")
                n, t_log = 0, time.time()
                try:
                    async for raw in ws:
                        p = hour_path(out, region)
                        if p != path:  # new UTC hour: rotate file
                            fh.close()
                            path, fh = p, gzip.open(p, "at")
                        fh.write(raw if isinstance(raw, str) else raw.decode())
                        fh.write("\n")
                        n += 1
                        if time.time() - t_log > 300:
                            print(f"{datetime.now(UTC):%H:%M}Z {region}: {n} msgs in 5 min", flush=True)
                            fh.flush()
                            n, t_log = 0, time.time()
                finally:
                    fh.close()
        except Exception as exc:  # network drops are expected overnight; keep going
            print(f"{datetime.now(UTC):%H:%M}Z reconnect in {backoff:.0f}s: {exc!r}", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="BALTIC_WATCH", choices=sorted(REGIONS))
    ap.add_argument("--out", default="data/cache/ais", type=Path)
    ap.add_argument("--env", default=".env")
    a = ap.parse_args()
    key = (dotenv_values(a.env).get("AISSTREAM_API_KEY") or "").split("#")[0].strip()
    if not key:
        raise SystemExit("AISSTREAM_API_KEY missing in .env")
    asyncio.run(record(a.region, a.out, key))


if __name__ == "__main__":
    main()
