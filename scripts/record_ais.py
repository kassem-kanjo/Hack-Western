"""Record the aisstream.io live feed for a region to hourly gzip JSONL files.

Why: Global Fishing Watch only publishes AIS gaps > 50 nm offshore and its per-vessel
presence API is down, so we build our own track history from the live feed and detect
silences ourselves. Runs unattended for hours: reconnects with backoff, never exits on
a network error, writes one file per UTC hour so a crash loses at most one partial hour.

Usage: uv run python scripts/record_ais.py --regions BALTIC_WATCH,HORMUZ --out data/cache/ais
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
    # Iranian-oil ship-to-ship hub off Johor/Singapore (Eastern Outer Port Limits)
    "MALAYSIA_EOPL": [[0.8, 103.3], [2.6, 105.8]],
    # Russian-oil ship-to-ship area off southern Greece
    "LACONIA": [[35.8, 21.8], [37.2, 23.6]],
    # Red Sea and Bab el-Mandeb
    "RED_SEA": [[11.5, 38.0], [20.5, 44.5]],
    "BLACK_SEA": [[40.5, 27.0], [47.5, 42.0]],
}
URL = "wss://stream.aisstream.io/v0/stream"


def hour_path(out: Path, region: str) -> Path:
    now = datetime.now(UTC)
    return out / region / f"{now:%Y%m%dT%H}.jsonl.gz"


def regions_for(lat: float, lon: float, regions: list[str]) -> list[str]:
    """Which recorded regions contain this position (boxes may overlap)."""
    out = []
    for r in regions:
        (la0, lo0), (la1, lo1) = REGIONS[r]
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            out.append(r)
    return out


async def record(regions: list[str], out: Path, key: str) -> None:
    """One WebSocket for all regions (aisstream limits connections per key); file each message by position."""
    backoff = 1.0
    while True:
        handles: dict[str, tuple[Path, gzip.GzipFile]] = {}
        try:
            async with websockets.connect(URL, ping_interval=20, max_size=2**22) as ws:
                boxes = [REGIONS[r] for r in regions]
                await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": boxes}))
                backoff = 1.0
                counts = {r: 0 for r in regions}
                t_log = time.time()
                async for raw in ws:
                    text = raw if isinstance(raw, str) else raw.decode()
                    try:
                        meta = json.loads(text).get("MetaData", {})
                        targets = regions_for(float(meta["latitude"]), float(meta["longitude"]), regions)
                    except (ValueError, KeyError, TypeError):
                        continue
                    for r in targets:
                        p = hour_path(out, r)
                        cur = handles.get(r)
                        if cur is None or cur[0] != p:  # first message or new UTC hour: rotate
                            if cur is not None:
                                cur[1].close()
                            p.parent.mkdir(parents=True, exist_ok=True)
                            handles[r] = (p, gzip.open(p, "at"))
                        handles[r][1].write(text + "\n")
                        counts[r] += 1
                    if time.time() - t_log > 300:
                        msg = ", ".join(f"{r} {n}" for r, n in counts.items())
                        print(f"{datetime.now(UTC):%H:%M}Z msgs in 5 min: {msg}", flush=True)
                        for _, fh in handles.values():
                            fh.flush()
                        counts = {r: 0 for r in regions}
                        t_log = time.time()
        except Exception as exc:  # network drops are expected overnight; keep going
            print(f"{datetime.now(UTC):%H:%M}Z reconnect in {backoff:.0f}s: {exc!r}", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            for _, fh in handles.values():
                fh.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", default="BALTIC_WATCH", help="comma-separated: " + ",".join(sorted(REGIONS)))
    ap.add_argument("--out", default="data/cache/ais", type=Path)
    ap.add_argument("--env", default=".env")
    a = ap.parse_args()
    regions = [r.strip() for r in a.regions.split(",") if r.strip()]
    unknown = [r for r in regions if r not in REGIONS]
    if unknown:
        raise SystemExit(f"unknown regions: {unknown}")
    key = (dotenv_values(a.env).get("AISSTREAM_API_KEY") or "").split("#")[0].strip()
    if not key:
        raise SystemExit("AISSTREAM_API_KEY missing in .env")
    asyncio.run(record(regions, a.out, key))


if __name__ == "__main__":
    main()
