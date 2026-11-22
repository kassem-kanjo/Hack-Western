"""Satellite check for dark alerts: what Sentinel-1 radar saw over the ship's reach.

    uv run --extra sat python scripts/satcheck.py --alert dark-230642000-1795306364
    uv run --extra sat python scripts/satcheck.py --top 3 --area BALTIC_WATCH \
        --data-dir "<repo>/data/snapshot" --ais-dir "<repo>/data/cache/ais/BALTIC_WATCH"

For each dark alert in ``watch.json``:

1. Area = the alert's reach circle at the end of its dark window, capped at 30 km
   (``darkfleet.core.satcheck``).
2. Time = Sentinel-1 RTC passes over that box from 48 h before the switch-off to now. The pass inside the
   dark window is used if there is one, else the nearer of the closest before and after (recorded).
3. Read the VV band of that box, mask land, run the CFAR detector, drop detections on the coast.
4. Match each detection to our own AIS at the pass time (+-15 min, 1.5 km). A detection with no match is
   "not broadcasting" only in the sense that our receivers did not hear it.
5. Write ``<data-dir>/satcheck/<alert_id>.json`` and a radar chip per unmatched detection in
   ``<data-dir>/satcheck/<alert_id>/NNN.png``. Nothing is invented: no scene, no file.

Served by ``GET /api/satcheck/{alert_id}``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer

from darkfleet.core.geo import distance_km
from darkfleet.core.sar_detect import DetectorParams, bright_land_mask, detect_ships, dilate
from darkfleet.core.sar_match import RADIUS_KM, TOLERANCE_S, AisFix, match_to_ais, position_at
from darkfleet.core.satcheck import (
    DATATAKE_GAP,
    SEARCH_BACK,
    choose_scene,
    dark_window,
    minutes_after_switch_off,
    rank_dark_alerts,
    reach_area,
)
from darkfleet.core.tracks import Ping
from darkfleet.ingest import planetary
from darkfleet.ingest.ais_tracks import iter_files

# The radar chip writer, land mask and coast rules are shared with the snapshot scan (scripts/sar_scan.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sar_scan  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "snapshot"
DEFAULT_AIS = ROOT / "data" / "cache" / "ais" / "BALTIC_WATCH"
ATTRIBUTION = "Contains modified Copernicus Sentinel-1 data, via Microsoft Planetary Computer"
LAND_BUFFER_M = 400.0
COAST_REJECT_M = 800.0
DEDUP_DEG = 0.002  # one ship seen in the overlap of two slices
# Open sea is about -22 dB in VV. Above this median the radar-bright land test would mask the sea
# itself (it treats anything brighter than -14 dB, over half a 31 px window, as land).
BRIGHT_SEA_DB = -16.0
AIS_PAD_S = TOLERANCE_S + 60  # the matcher interpolates inside TOLERANCE_S; read a little more
RECORDER = "BALTIC_WATCH"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ais_tracks_near(ais_dir: Path, when: datetime, pad_s: int) -> dict[int, list[AisFix]]:
    """Every AIS position within ``pad_s`` of ``when``, from the hourly recorder files of ``ais_dir``."""
    hours = {(when + timedelta(seconds=s)).strftime("%Y%m%dT%H") for s in (-pad_s, 0, pad_s)}
    paths = [ais_dir / f"{h}.jsonl.gz" for h in sorted(hours) if (ais_dir / f"{h}.jsonl.gz").is_file()]
    lo, hi = when - timedelta(seconds=pad_s), when + timedelta(seconds=pad_s)
    tracks: dict[int, list[AisFix]] = defaultdict(list)
    for obj in iter_files(paths):
        if isinstance(obj, Ping) and lo <= obj.t <= hi:
            tracks[obj.mmsi].append(AisFix(obj.t, obj.lat, obj.lon))
    for fixes in tracks.values():
        fixes.sort(key=lambda f: f.t)
    return tracks


def recorder_hours(ais_dir: Path) -> str:
    """The first and last hour the recorder holds, for the note when a pass has no AIS beside it."""
    names = sorted(p.name[:11] for p in ais_dir.glob("*.jsonl.gz")) if ais_dir.is_dir() else []
    return f"{names[0]} to {names[-1]}" if names else "nothing recorded"


def detect_in_box(
    items: list[planetary.Item],
    bbox: tuple[float, float, float, float],
    centre: tuple[float, float],
    radius_km: float,
) -> tuple[list[dict[str, Any]], int, list[float]]:
    """Radar detections inside the reach circle, each with its chip.

    Also returns how many slices were read, and the sea level (median dB) of each slice whose
    radar-bright land test was skipped. That test calls any patch brighter than -14 dB land, which
    a storm-roughened sea also does, so for such a slice only the vector coastline masks land.
    """
    params = DetectorParams()
    detections: list[dict[str, Any]] = []
    read = 0
    bright_skipped: list[float] = []
    for item in items:
        href = item.assets.get("vv")
        if not href:
            log(f"{item.id}: no VV band")
            continue
        t0 = time.time()
        win = planetary.read_window(planetary.sign(item.collection, href), bbox)
        if win is None:
            log(f"{item.id}: no coverage of the box")
            continue
        read += 1
        land = sar_scan.land_mask(win, LAND_BUFFER_M)
        positive = win.data[np.isfinite(win.data) & (win.data > 0)]
        sea_db = float(10 * np.log10(np.median(positive))) if positive.size else float("nan")
        if sea_db > BRIGHT_SEA_DB:
            bright_skipped.append(sea_db)
            log(f"{item.id}: sea median {sea_db:.1f} dB is bright; radar-bright land test skipped")
        else:
            land |= bright_land_mask(win.data)
        land |= ~np.isfinite(win.data)
        found = detect_ships(win.data, land, params)
        near = dilate(land, int(round(COAST_REJECT_M / abs(win.transform.a))))
        found = [d for d in found if not near[int(d.row), int(d.col)]]
        to_ll = Transformer.from_crs(win.crs, "EPSG:4326", always_xy=True)
        for d in found:
            x, y = win.transform * (d.col + 0.5, d.row + 0.5)
            lon, lat = to_ll.transform(x, y)
            if distance_km(centre[1], centre[0], lat, lon) > radius_km:
                continue  # inside the square box, outside the circle
            if any(abs(e["lat"] - lat) < DEDUP_DEG and abs(e["lon"] - lon) < DEDUP_DEG for e in detections):
                continue
            detections.append(
                {
                    "lat": round(float(lat), 5),
                    "lon": round(float(lon), 5),
                    "peak_db": round(d.peak_db, 1),
                    "contrast_db": round(d.contrast_db, 1),
                    "matched_mmsi": None,
                    "chip_url": None,
                    "_chip": sar_scan.radar_chip(win.data, d.row, d.col),
                }
            )
        log(f"{item.id}: {len(found)} detections, {time.time() - t0:.0f}s")
        del win
    return detections, read, bright_skipped


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write via a temp file, so the API never reads half a result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def no_scene_payload(
    alert: dict[str, Any], now: datetime, search_from: datetime, window: Any
) -> dict[str, Any]:
    return {
        "alert_id": alert["id"],
        "mmsi": alert["mmsi"],
        "name": alert.get("name"),
        "generated_at": iso(now),
        "status": "no_scene",
        "dark_window": {
            "start": iso(window.start),
            "end": iso(window.end) if window.end else None,
            "ongoing": window.ongoing,
        },
        "search": {"from": iso(search_from), "to": iso(now)},
        "area": None,
        "scene": None,
        "detections": [],
        "counts": None,
        "ais": {"vessels_heard": 0, "recording": RECORDER, "coverage_note": ""},
        "notes": [f"Searched radar passes from {search_from:%d %b %H:%M} UTC to now. None covers this area."],
        "source": ATTRIBUTION,
    }


def scan_alert(alert: dict[str, Any], *, now: datetime, data_dir: Path, ais_dir: Path) -> dict[str, Any]:
    """Run the satellite check for one alert, write its JSON and chips, and return the payload."""
    t_all = time.time()
    alert_id = alert["id"]
    window = dark_window(alert)
    area = reach_area(alert, now)
    bbox = area.bbox
    search_from = window.start - SEARCH_BACK
    log(
        f"{alert_id}: circle {area.radius_km:.1f} km (reach {area.reach_km:.1f}), "
        f"search {search_from:%m-%d %H:%M} to now"
    )

    items = planetary.search("sentinel-1-rtc", bbox, search_from, now)
    choice = choose_scene([i.time for i in items], window, now)
    if choice is None:
        payload = no_scene_payload(alert, now, search_from, window)
        write_json_atomic(data_dir / "satcheck" / f"{alert_id}.json", payload)
        log(f"{alert_id}: no scene")
        return payload

    anchor = items[choice.index]
    datatake = [
        i for i in items if abs((i.time - anchor.time).total_seconds()) <= DATATAKE_GAP.total_seconds()
    ]
    log(f"{alert_id}: scene {anchor.id} {choice.relation}, {len(datatake)} slice(s)")
    dets, read, bright_skipped = detect_in_box(
        datatake, bbox, (area.center_lon, area.center_lat), area.radius_km
    )
    if read == 0:
        raise RuntimeError(f"{alert_id}: scene {anchor.id} listed but none of its slices could be read")

    when = anchor.time
    tracks = ais_tracks_near(ais_dir, when, AIS_PAD_S)
    heard = sum(1 for fixes in tracks.values() if position_at(fixes, when) is not None)
    matches = match_to_ais([(d["lat"], d["lon"]) for d in dets], tracks, when)

    chips_dir = data_dir / "satcheck" / alert_id
    if chips_dir.is_dir():
        for old in chips_dir.glob("*.png"):
            old.unlink()
    n_chip = 0
    for d, mmsi in zip(dets, matches, strict=True):
        chip = d.pop("_chip")
        d["matched_mmsi"] = mmsi
        if mmsi is None:
            name = f"{n_chip:03d}.png"
            sar_scan.save_png(chip, chips_dir / name)
            d["chip_url"] = f"/api/satcheck/{alert_id}/chip/{name}"
            n_chip += 1

    matched = sum(1 for m in matches if m is not None)
    not_broadcasting = len(dets) - matched if heard > 0 else None
    minutes = minutes_after_switch_off(when, window)
    # Short on purpose: the panel's own sentence and meta line already say when and how big.
    notes: list[str] = []
    if area.capped:
        notes.append(
            f"Only a {area.radius_km:.0f} km circle was read; the ship could have sailed "
            f"{area.reach_km:.0f} km."
        )
    ais_note = ""
    if heard == 0:
        ais_note = (
            f"No AIS from our {RECORDER} recorder near the pass time (recorded {recorder_hours(ais_dir)}), "
            "so no ship is marked not broadcasting."
        )
        notes.append(ais_note)
    else:
        notes.append(
            f"Matched: our AIS heard the ship within {RADIUS_KM:.1f} km at the pass time. "
            "Not broadcasting only means our receivers did not hear it."
        )
    if read < len(datatake):
        notes.append(f"{len(datatake) - read} of {len(datatake)} radar slices had no coverage of the box.")
    if bright_skipped:
        notes.append(
            f"The sea is unusually bright in this pass (median {max(bright_skipped):.0f} dB; open sea is "
            "about -22 dB), so the radar-bright land test was skipped. Land comes from the coastline only."
        )

    payload = {
        "alert_id": alert_id,
        "mmsi": alert["mmsi"],
        "name": alert.get("name"),
        "generated_at": iso(now),
        "status": "computed",
        "dark_window": {
            "start": iso(window.start),
            "end": iso(window.end) if window.end else None,
            "ongoing": window.ongoing,
        },
        "search": {"from": iso(search_from), "to": iso(now)},
        "area": {
            "center": [round(area.center_lon, 5), round(area.center_lat, 5)],
            "radius_km": round(area.radius_km, 1),
            "reach_km": round(area.reach_km, 1),
            "capped": area.capped,
            "bbox": [round(v, 4) for v in bbox],
        },
        "scene": {
            "id": anchor.id,
            "time": iso(when),
            "relation": choice.relation,
            "minutes_after_switch_off": minutes,
            "datatake_ids": [i.id for i in datatake],
        },
        "detections": dets,
        "counts": {
            "detected": len(dets),
            "matched": matched,
            "not_broadcasting": not_broadcasting,
        },
        "ais": {"vessels_heard": heard, "recording": RECORDER, "coverage_note": ais_note},
        "notes": notes,
        "detector": {"name": "two-parameter CFAR", "k": DetectorParams().k, "pixel_m": 10},
        "source": ATTRIBUTION,
        "runtime_s": round(time.time() - t_all, 1),
    }
    write_json_atomic(data_dir / "satcheck" / f"{alert_id}.json", payload)
    log(
        f"{alert_id}: {choice.relation} {iso(when)} ({minutes:+d} min), detected {len(dets)}, "
        f"matched {matched}, not broadcasting {not_broadcasting}, {payload['runtime_s']}s"
    )
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alert", action="append", default=[], help="alert id from watch.json (repeatable)")
    ap.add_argument(
        "--top", type=int, default=0, help="the N strongest dark alerts (most ships it could have met)"
    )
    ap.add_argument("--area", default=None, help="limit --top to one watch area, e.g. BALTIC_WATCH")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--ais-dir", type=Path, default=DEFAULT_AIS)
    args = ap.parse_args()

    watch = json.loads((args.data_dir / "watch.json").read_text(encoding="utf-8"))
    alerts = watch["alerts"]
    by_id = {a["id"]: a for a in alerts}
    targets: list[dict[str, Any]] = []
    for alert_id in args.alert:
        if alert_id not in by_id:
            log(f"{alert_id}: not in watch.json")
            return 2
        targets.append(by_id[alert_id])
    if args.top:
        targets += [
            a
            for a in rank_dark_alerts(alerts, area_id=args.area, limit=args.top)
            if a["id"] not in {t["id"] for t in targets}
        ]
    if not targets:
        log("nothing to check: give --alert or --top")
        return 2

    results = []
    failed = 0
    for alert in targets:
        now = datetime.now(UTC)
        try:
            payload = scan_alert(alert, now=now, data_dir=args.data_dir, ais_dir=args.ais_dir)
        except Exception as exc:  # one failed alert must not stop the rest; nothing is written for it
            failed += 1
            log(f"{alert['id']}: FAILED {type(exc).__name__}: {exc}")
            continue
        results.append(payload)

    for p in results:
        s = p.get("scene") or {}
        c = p.get("counts") or {}
        print(
            f"{p['alert_id']}\t{p['status']}\t{s.get('time', '-')}\t{s.get('relation', '-')}\t"
            f"{s.get('minutes_after_switch_off', '-')}\t{c.get('detected', '-')}\t"
            f"{c.get('not_broadcasting', '-')}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
