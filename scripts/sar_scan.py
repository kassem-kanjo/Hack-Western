"""Detect ships ourselves on the freshest Sentinel-1 radar of the Strait of Hormuz.

    uv run --extra sat python scripts/sar_scan.py [--days 10] [--ais-dir data/cache/ais/HORMUZ]

1. Find the newest Sentinel-1 acquisition over the bbox on Microsoft Planetary Computer (free, no login)
   and read only the bbox window of its VV band (radiometrically terrain-corrected, 10 m, linear power).
2. Mask land (Natural Earth 10 m, grown by ``--land-buffer-m``) and run the CFAR detector
   (``darkfleet.core.sar_detect``).
3. Match each detection to our own AIS recording at the scene time (+-15 min, 1.5 km).
4. Write ``data/snapshot/sar_scan.json`` and a 128x128 radar chip per unmatched detection, plus (best
   effort) a 256x256 Sentinel-2 true-colour chip from the least cloudy scene of the same day.

"Unmatched" means "not heard by OUR receivers", not "dark": our recorder has few receivers.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from darkfleet.core.sar_detect import DetectorParams, bright_land_mask, detect_ships, dilate
from darkfleet.core.sar_match import RADIUS_KM, TOLERANCE_S, AisFix, match_to_ais
from darkfleet.ingest import planetary

ROOT = Path(__file__).resolve().parents[1]
BBOX = (55.8, 25.8, 57.0, 26.9)  # Strait of Hormuz, lon/lat
LAND = ROOT / "web" / "public" / "basemap" / "land-10m.geojson"
OUT_DIR = ROOT / "data" / "snapshot"
CHIP_PX = 128
OPTICAL_PX = 256
OPTICAL_LOOKBACK_DAYS = 6


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def load_ais(ais_dir: Path, when: datetime, pad_s: float) -> tuple[dict[int, list[AisFix]], dict]:
    """AIS position reports within ``pad_s`` of ``when`` from the hourly recorder files."""
    tracks: dict[int, list[AisFix]] = defaultdict(list)
    files = 0
    first = last = None
    hours = {(when + timedelta(seconds=s)).strftime("%Y%m%dT%H") for s in (-pad_s, 0, pad_s)}
    all_files = sorted(ais_dir.glob("*.jsonl.gz")) if ais_dir.is_dir() else []
    if all_files:
        first, last = all_files[0].name[:11], all_files[-1].name[:11]
    for name in sorted(hours):
        path = ais_dir / f"{name}.jsonl.gz"
        if not path.is_file():
            continue
        files += 1
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        meta = json.loads(line)["MetaData"]
                        t = datetime.strptime(meta["time_utc"][:26], "%Y-%m-%d %H:%M:%S.%f").replace(
                            tzinfo=UTC
                        )
                        tracks[int(meta["MMSI"])].append(
                            AisFix(t, float(meta["latitude"]), float(meta["longitude"]))
                        )
                    except (ValueError, KeyError, TypeError):
                        continue
        except EOFError:  # the hour still being written
            pass
    for fixes in tracks.values():
        fixes.sort(key=lambda f: f.t)
    return tracks, {"files_read": files, "recording_first_hour": first, "recording_last_hour": last}


def land_mask(window: planetary.Window, buffer_m: float) -> np.ndarray:
    """True on land (and ``buffer_m`` beyond it) for every pixel of the window."""
    import shapely
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.warp import transform_bounds
    from shapely.geometry import box

    from darkfleet.core.water import load_land

    h, w = window.data.shape
    left, top = window.transform * (0, 0)
    right, bottom = window.transform * (w, h)
    lo = transform_bounds(window.crs, "EPSG:4326", left, bottom, right, top, densify_pts=21)
    land = load_land(LAND).intersection(box(lo[0] - 0.05, lo[1] - 0.05, lo[2] + 0.05, lo[3] + 0.05))
    if land.is_empty:
        return np.zeros((h, w), dtype=bool)
    to_utm = Transformer.from_crs("EPSG:4326", window.crs, always_xy=True)

    def project(coords: np.ndarray) -> np.ndarray:
        x, y = to_utm.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    shapes = [(shapely.transform(g, project), 1) for g in getattr(land, "geoms", [land])]
    mask = rasterize(shapes, out_shape=(h, w), transform=window.transform, fill=0, dtype="uint8").astype(bool)
    return dilate(mask, int(round(buffer_m / abs(window.transform.a))))


def radar_chip(data: np.ndarray, row: float, col: float) -> np.ndarray:
    """128x128 contrast-stretched (dB, 2..99.8 percentile of the crop) 8-bit crop around a detection."""
    half = CHIP_PX // 2
    r0, c0 = int(round(row)) - half, int(round(col)) - half
    pad = np.full((CHIP_PX, CHIP_PX), np.nan, dtype=np.float32)
    rs, cs = max(r0, 0), max(c0, 0)
    re, ce = min(r0 + CHIP_PX, data.shape[0]), min(c0 + CHIP_PX, data.shape[1])
    pad[rs - r0 : re - r0, cs - c0 : ce - c0] = data[rs:re, cs:ce]
    db = 10.0 * np.log10(np.where(pad > 0, pad, np.nan))
    finite = db[np.isfinite(db)]
    if finite.size < 10:
        return np.zeros((CHIP_PX, CHIP_PX), dtype=np.uint8)
    lo, hi = np.percentile(finite, [2, 99.8])
    img = np.clip((np.nan_to_num(db, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)
    return (img * 255).astype(np.uint8)


def save_png(array: np.ndarray, path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path, optimize=True)


def optical_chip(lat: float, lon: float, day: datetime) -> tuple[np.ndarray | None, dict | None]:
    """256x256 true-colour Sentinel-2 L2A chip from a clear recent scene covering the point."""
    half_lat = OPTICAL_PX / 2 * 10.0 / 111_320.0
    half_lon = half_lat / np.cos(np.radians(lat))
    box = (lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat)
    # Sentinel-2 revisits every 2-5 days, so the same day often has no scene: take the newest scene up to
    # OPTICAL_LOOKBACK_DAYS before the radar pass, preferring clear ones. A moving ship will not be in it.
    end = day.replace(hour=23, minute=59, second=59, microsecond=0)
    items = planetary.search("sentinel-2-l2a", box, end - timedelta(days=OPTICAL_LOOKBACK_DAYS), end)
    usable = (i for i in items if i.cloud is not None)
    items = sorted(usable, key=lambda i: (i.cloud > 30, -i.time.timestamp()))
    for item in items[:3]:
        bands = []
        for name in ("B04", "B03", "B02"):
            win = planetary.read_window(planetary.sign(item.collection, item.assets[name]), box)
            if win is None:
                break
            bands.append(win.data)
        if len(bands) < 3:
            continue
        hgt, wid = min(b.shape[0] for b in bands), min(b.shape[1] for b in bands)
        rgb = np.stack([b[:hgt, :wid] for b in bands], axis=-1)
        if (rgb == 0).mean() > 0.2 or not np.isfinite(rgb).all():
            continue  # outside the swath
        ref = np.clip((rgb - 1000.0) / 10000.0, 0, None)  # processing baseline >= 4.00 adds a 1000 offset
        # Open sea reflects ~1-3%: stretch each chip so its 99.7th percentile is white (ships and cloud clip).
        scale = max(float(np.percentile(ref, 99.7)), 0.03)
        img = (np.clip(ref / scale, 0, 1) ** (1 / 1.3) * 255).astype(np.uint8)
        top, left = max((hgt - OPTICAL_PX) // 2, 0), max((wid - OPTICAL_PX) // 2, 0)
        img = img[top : top + OPTICAL_PX, left : left + OPTICAL_PX]
        out = np.zeros((OPTICAL_PX, OPTICAL_PX, 3), dtype=np.uint8)
        out[: img.shape[0], : img.shape[1]] = img
        return out, {"id": item.id, "time": item.time.isoformat(), "cloud_pct": item.cloud}
    return None, None


def attach_optical(d: dict, chips_dir: Path, name: str, when: datetime) -> None:
    img = meta = None
    for attempt in range(3):
        try:
            img, meta = optical_chip(d["lat"], d["lon"], when)
            break
        except Exception as exc:  # network or catalogue hiccup must not lose the radar result
            log(f"optical chip {name} attempt {attempt + 1} failed: {type(exc).__name__}")
            time.sleep(5)
    if img is not None:
        save_png(img, chips_dir / f"{name}_optical.png")
        d["optical_chip"] = f"{name}_optical.png"
        d["optical_scene"] = meta


def top_up_optical(args: argparse.Namespace) -> int:
    path = args.out_dir / "sar_scan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    when = datetime.fromisoformat(payload["time"])
    tried = 0
    for d in payload["detections"]:
        if d.get("chip") and not d.get("optical_chip") and tried < args.max_optical:
            tried += 1
            attach_optical(d, args.out_dir / "chips", d["chip"].removesuffix(".png"), when)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    log(f"optical chips: {sum(1 for d in payload['detections'] if d.get('optical_chip'))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--ais-dir", type=Path, default=ROOT / "data" / "cache" / "ais" / "HORMUZ")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("-k", type=float, default=DetectorParams.k)
    ap.add_argument("--land-buffer-m", type=float, default=400.0)
    ap.add_argument("--coast-reject-m", type=float, default=800.0, help="drop detections this close to land")
    ap.add_argument("--max-optical", type=int, default=12, help="optical chips to try (they are slow)")
    ap.add_argument("--no-optical", action="store_true")
    ap.add_argument("--optical-only", action="store_true", help="add missing optical chips to the last scan")
    ap.add_argument("--item", help="scan this S1 item id instead of the newest")
    args = ap.parse_args()

    if args.optical_only:
        return top_up_optical(args)
    t_start = time.time()
    now = datetime.now(UTC)
    items = planetary.search("sentinel-1-rtc", BBOX, now - timedelta(days=args.days), now)
    if not items:
        log("no Sentinel-1 scene in range")
        return 1
    if args.item:
        scene_items = [i for i in items if i.id == args.item]
    else:
        scene_items = planetary.group_datatake(items)
    log(f"scenes: {[i.id for i in scene_items]}")

    params = DetectorParams(k=args.k)
    detections: list[dict] = []
    chips_data: dict[int, np.ndarray] = {}
    coverage = []
    read_s = detect_s = 0.0
    rejected_coast = 0
    for item in scene_items:
        t0 = time.time()
        win = planetary.read_window(planetary.sign(item.collection, item.assets["vv"]), BBOX)
        read_s += time.time() - t0
        if win is None:
            continue
        h, w = win.data.shape
        log(f"{item.id}: window {h}x{w} read in {time.time() - t0:.0f}s")
        t0 = time.time()
        land = land_mask(win, args.land_buffer_m)
        land |= bright_land_mask(win.data)  # radar-bright shores the vector coastline misses
        land |= ~np.isfinite(win.data)
        found_all = detect_ships(win.data, land, params)
        near = dilate(land, int(round(args.coast_reject_m / abs(win.transform.a))))
        found = [d for d in found_all if not near[int(d.row), int(d.col)]]
        rejected_coast += len(found_all) - len(found)
        detect_s += time.time() - t0
        log(f"{item.id}: {len(found)} detections, land {land.mean():.1%} of window, {time.time() - t0:.0f}s")
        from pyproj import Transformer

        to_ll = Transformer.from_crs(win.crs, "EPSG:4326", always_xy=True)
        xs = [win.transform * (d.col + 0.5, d.row + 0.5) for d in found]
        lons, lats = to_ll.transform([x[0] for x in xs], [x[1] for x in xs]) if xs else ([], [])
        left, top = win.transform * (0, 0)
        right, bottom = win.transform * (w, h)
        lo = to_ll.transform(left, bottom), to_ll.transform(right, top)
        coverage.append(
            {
                "scene_id": item.id,
                "bbox": [round(lo[0][0], 4), round(lo[0][1], 4), round(lo[1][0], 4), round(lo[1][1], 4)],
                "sea_fraction": round(float(1 - land.mean()), 3),
            }
        )
        for d, lat, lon in zip(found, lats, lons, strict=True):
            if any(abs(e["lat"] - lat) < 0.002 and abs(e["lon"] - lon) < 0.002 for e in detections):
                continue  # seen by the overlap of two slices
            idx = len(detections)
            detections.append(
                {
                    "lat": round(float(lat), 5),
                    "lon": round(float(lon), 5),
                    "area_px": d.area_px,
                    "peak_db": round(d.peak_db, 1),
                    "contrast_db": round(d.contrast_db, 1),
                    "scene_id": item.id,
                    "matched_mmsi": None,
                }
            )
            chips_data[idx] = radar_chip(win.data, d.row, d.col)
        del win

    primary = scene_items[0]
    when = primary.time
    tracks, ais_info = load_ais(args.ais_dir, when, TOLERANCE_S)
    usable = sum(
        1 for f in tracks.values() if any(abs((x.t - when).total_seconds()) <= TOLERANCE_S for x in f)
    )
    matches = match_to_ais([(d["lat"], d["lon"]) for d in detections], tracks, when)
    for d, m in zip(detections, matches, strict=True):
        d["matched_mmsi"] = m
    n_match = sum(m is not None for m in matches)

    if usable == 0:
        note = (
            f"No AIS from our HORMUZ recorder within +-{TOLERANCE_S // 60} min of the scene "
            f"({when:%Y-%m-%d %H:%M} UTC): the recording covers hours {ais_info['recording_first_hour']} to "
            f"{ais_info['recording_last_hour']} UTC. Nothing could be matched, so every detection is "
            "'unmatched' only because we had no AIS to compare with."
        )
    else:
        note = (
            f"Matched against our own AIS recorder ({usable} vessels heard within "
            f"+-{TOLERANCE_S // 60} min of "
            "the scene, few receivers around Hormuz). 'Unmatched' means not heard by OUR receivers; it does "
            "not mean the ship had its transponder off."
        )

    chips_dir = args.out_dir / "chips"
    if chips_dir.is_dir():
        for old in chips_dir.glob("sar_*.png"):
            old.unlink()
    stamp = when.strftime("%Y%m%dT%H%M")
    optical_tried = 0
    for idx, d in enumerate(detections):
        if d["matched_mmsi"] is not None:
            continue
        name = f"sar_{stamp}_{idx:03d}"
        save_png(chips_data[idx], chips_dir / f"{name}.png")
        d["chip"] = f"{name}.png"
        if not args.no_optical and optical_tried < args.max_optical:
            optical_tried += 1
            attach_optical(d, chips_dir, name, when)

    payload = {
        "scene_id": primary.id,
        "scene_ids": [i.id for i in scene_items],
        "time": when.isoformat(),
        "bbox": list(BBOX),
        "coverage": coverage,
        "source": (
            "Copernicus Sentinel-1 (ESA), RTC processing by Microsoft Planetary Computer; "
            "ships found by Dark Fleet's own CFAR detector"
        ),
        "detector": {
            "name": "two-parameter CFAR",
            "k": params.k,
            "guard_px": params.guard,
            "ring_px": params.ring,
            "min_peak_contrast_db": round(10 * float(np.log10(params.min_peak_ratio)), 1),
            "min_area_px": params.min_area,
            "max_area_px": params.max_area,
            "pixel_m": 10,
            "land_buffer_m": args.land_buffer_m,
            "coast_reject_m": args.coast_reject_m,
            "rejected_near_coast": rejected_coast,
        },
        "match": {"tolerance_min": TOLERANCE_S // 60, "radius_km": RADIUS_KM},
        "counts": {"detected": len(detections), "matched": n_match, "unmatched": len(detections) - n_match},
        "optical_note": (
            "Optical chips are the newest clear Sentinel-2 scene up to "
            f"{OPTICAL_LOOKBACK_DAYS} days before the radar pass (Sentinel-2 revisits every 2-5 days and has "
            "no scene on the same day). Ships move, so they are context for the sea and coast, not proof."
        ),
        "ais_vessels_near_scene_time": usable,
        "ais_coverage_note": note,
        "generated_at": datetime.now(UTC).isoformat(),
        "runtime_s": round(time.time() - t_start, 1),
        "detections": detections,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "sar_scan.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    log(f"done: {payload['counts']} read {read_s:.0f}s detect {detect_s:.0f}s total {payload['runtime_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
