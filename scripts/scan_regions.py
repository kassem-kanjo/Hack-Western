"""Feasibility gate F4: find 5x5 degree regions where real GFW AIS-gap prisms contain SAR detections.

    ~/.local/bin/uv run python scripts/scan_regions.py [--start 2026-08-15 --end 2026-11-15]

Own httpx calls (src/darkfleet/ingest/gfw.py is types only). Every response is cached under
data/cache/scan/, so re-runs make no API calls. The token is only sent in the Authorization header.
Writes data/cases/scan_results.json and data/cases/best_cases.json; docs/region-scan.md is written by hand
from them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from darkfleet.core.geo import KNOT_KM_H, Fix, distance_km_array  # noqa: E402
from darkfleet.core.match import SarCell, candidate_sightings  # noqa: E402
from darkfleet.core.speeds import max_speed_knots  # noqa: E402

load_dotenv(ROOT / ".env")
BASE = "https://gateway.api.globalfishingwatch.org/v3"
CACHE = ROOT / "data" / "cache" / "scan"
CASES = ROOT / "data" / "cases"
CACHE.mkdir(parents=True, exist_ok=True)
CASES.mkdir(parents=True, exist_ok=True)
REQUESTS = 0  # live (uncached) calls


def _client() -> httpx.Client:
    return httpx.Client(headers={"Authorization": "Bearer " + os.environ["GFW_API_TOKEN"]}, timeout=300)


def cached_post(cli, url, params, body):
    global REQUESTS
    key = hashlib.sha256(json.dumps([url, params, body], sort_keys=True).encode()).hexdigest()[:24]
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(4):
        r = cli.post(url, params=params, json=body)
        REQUESTS += 1
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            import time

            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    data = r.json()
    f.write_text(json.dumps(data))
    return data


def parse_t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def fetch_gaps(cli, start, end):
    out, offset = [], 0
    while offset is not None:
        d = cached_post(
            cli,
            f"{BASE}/events",
            {"limit": 200, "offset": offset},
            {"datasets": ["public-global-gaps-events:latest"], "startDate": start, "endDate": end},
        )
        out += d["entries"]
        offset = d.get("nextOffset")
    return out


def to_gap(e):
    g = e["gap"]
    v = e["vessel"]
    return {
        "id": e["id"],
        "name": v.get("name"),
        "mmsi": v.get("ssvid"),
        "flag": v.get("flag"),
        "type": v.get("type"),
        "start": parse_t(e["start"]),
        "end": parse_t(e["end"]),
        "off": (g["offPosition"]["lat"], g["offPosition"]["lon"]),
        "on": (g["onPosition"]["lat"], g["onPosition"]["lon"]),
        "hours": float(g["durationHours"]),
        "disabling": g.get("intentionalDisabling"),
    }


def midpoint(g):
    return ((g["off"][0] + g["on"][0]) / 2, (g["off"][1] + g["on"][1]) / 2)


def fetch_sar(cli, bbox, d0: date, d1: date):
    """Unmatched SAR cells in bbox=(w,s,e,n) for days d0..d1 inclusive, split into <=31-day calls."""
    w, s, e, n = bbox
    poly = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    rows, a = [], d0
    while a <= d1:
        b = min(a + timedelta(days=30), d1)
        d = cached_post(
            cli,
            f"{BASE}/4wings/report",
            {
                "spatial-resolution": "HIGH",
                "temporal-resolution": "DAILY",
                "datasets[0]": "public-global-sar-presence:latest",
                "date-range": f"{a},{b}",
                "format": "JSON",
                "filters[0]": "matched='false'",
            },
            {"geojson": poly},
        )
        for ent in d.get("entries", []):
            for v in ent.values():
                if isinstance(v, list):
                    rows += v
        a = b + timedelta(days=1)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-15")
    ap.add_argument("--end", default="2026-11-15")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--pad", type=float, default=1.0, help="degrees added around each cell for SAR")
    args = ap.parse_args()
    cli = _client()

    raw = fetch_gaps(cli, args.start, args.end)
    gaps = [to_gap(e) for e in raw]
    n_all = len(gaps)
    gaps = [g for g in gaps if g["type"] != "gear" and 6 <= g["hours"] <= 96]
    print(f"events {n_all}, kept {len(gaps)} (non-gear, 6-96 h); live requests so far {REQUESTS}")

    cells = defaultdict(list)
    for g in gaps:
        la, lo = midpoint(g)
        cells[(int(np.floor(la / 5) * 5), int(np.floor(lo / 5) * 5))].append(g)
    top = sorted(cells.items(), key=lambda kv: -len(kv[1]))[: args.top]

    win0, win1 = date.fromisoformat(args.start), date.fromisoformat(args.end)
    summary, all_cases = [], []
    for (la0, lo0), gl in top:
        bbox = (lo0 - args.pad, la0 - args.pad, lo0 + 5 + args.pad, la0 + 5 + args.pad)
        d0 = max(min(g["start"] for g in gl).date(), win0)
        d1 = min(max(g["end"] for g in gl).date(), win1)
        rows = fetch_sar(cli, bbox, d0, d1)
        sar = [(r["lat"], r["lon"], date.fromisoformat(r["date"][:10]), r.get("detections", 1)) for r in rows]
        slat = np.array([x[0] for x in sar])
        slon = np.array([x[1] for x in sar])
        sday = np.array([x[2].toordinal() for x in sar])
        cases = []
        for g in gl:
            v = max_speed_knots(g["type"])
            reach = v * KNOT_KM_H * g["hours"]
            if len(sar):
                m = (sday >= g["start"].date().toordinal()) & (sday <= g["end"].date().toordinal())
                idx = np.where(m)[0]
                if len(idx):  # cheap footprint prefilter: d(a,p)+d(p,b) <= reach
                    da = distance_km_array(g["off"][0], g["off"][1], slat[idx], slon[idx])
                    db = distance_km_array(slat[idx], slon[idx], g["on"][0], g["on"][1])
                    idx = idx[da + db <= reach + 1e-3]
            else:
                idx = []
            cands = []
            if len(idx):
                sc = [SarCell(sar[i][0], sar[i][1], sar[i][2]) for i in idx]
                a = Fix(g["off"][0], g["off"][1], g["start"])
                b = Fix(g["on"][0], g["on"][1], g["end"])
                cands = candidate_sightings(a, b, v, sc)
            cases.append((g, cands, [sar[i][3] for i in idx] if len(idx) else []))
        with_c = [c for c in cases if c[1]]
        types = Counter(g["type"] for g in gl).most_common(3)
        summary.append(
            {
                "cell": f"{la0}..{la0 + 5}N, {lo0}..{lo0 + 5}E",
                "bbox_sw": [la0, lo0],
                "gaps": len(gl),
                "gaps_with_candidates": len(with_c),
                "total_candidates": sum(len(c[1]) for c in cases),
                "sar_cells_fetched": len(sar),
                "top_types": types,
            }
        )
        print(summary[-1], f"live requests {REQUESTS}")
        for g, cands, _ in with_c:
            all_cases.append(
                {
                    "cell": summary[-1]["cell"],
                    "vessel": g["name"],
                    "mmsi": g["mmsi"],
                    "flag": g["flag"],
                    "type": g["type"],
                    "off_time": g["start"].isoformat(),
                    "on_time": g["end"].isoformat(),
                    "off": g["off"],
                    "on": g["on"],
                    "hours": round(g["hours"], 1),
                    "intentional_disabling": g["disabling"],
                    "n_candidates": len(cands),
                    "candidates": [
                        {
                            "lat": c.cell.lat,
                            "lon": c.cell.lon,
                            "day": c.cell.day.isoformat(),
                            "slack": round(c.slack, 3),
                        }
                        for c in sorted(cands, key=lambda c: -c.slack)[:200]
                    ],
                }
            )

    # Rank: fewer candidates = more specific story; need >=1. Prefer non-fishing, intentional disabling.
    def score(c):
        return (
            c["type"] != "fishing",
            bool(c["intentional_disabling"]),
            -abs(c["n_candidates"] - 3),
            c["hours"],
        )

    all_cases.sort(key=score, reverse=True)
    (CASES / "scan_results.json").write_text(
        json.dumps({"cells": summary, "requests": REQUESTS}, indent=1, default=str)
    )
    (CASES / "best_cases.json").write_text(json.dumps(all_cases[:20], indent=1, default=str))
    print(f"cases with candidates: {len(all_cases)}; live requests {REQUESTS}")


if __name__ == "__main__":
    main()
