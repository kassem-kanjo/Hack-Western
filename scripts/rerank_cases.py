"""Re-rank the region scan's SAR candidates with the tighter class speed caps (#28).

    ~/.local/bin/uv run python scripts/rerank_cases.py [--root "/Users/kassemkanjo/Desktop/Hack Western"]

Reads only the cached Global Fishing Watch responses under ``<root>/data/cache/scan/`` (a cache miss
is a bug, never a live request). The one live call is the Copernicus STAC search, cached under
``<root>/data/cache/stac/``. Writes ``<root>/data/cases/cases_v2.json`` and
``<root>/data/cases/rerank_counts.json``, plus ``tests/fixtures/api/case.json`` in this worktree.
Does NOT load .env and never touches the live GFW API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from darkfleet.core.filters import is_stationary, recurring_cells  # noqa: E402
from darkfleet.core.geo import KNOT_KM_H, Fix, distance_km, distance_km_array  # noqa: E402
from darkfleet.core.match import SarCell, candidate_sightings, verdict_at_scene  # noqa: E402
from darkfleet.core.prism import footprint_polygon, lens_polygon  # noqa: E402
from darkfleet.core.speeds import max_speed_knots  # noqa: E402
from darkfleet.ingest.stac import scene_times  # noqa: E402

BASE = "https://gateway.api.globalfishingwatch.org/v3"
START = "2026-08-15"
END = "2026-11-15"
N_GAPS = 5624
PAD = 1.0
MARGIN = 1.15
TOP_CELLS = 15
STATIONARY_KM = 5.0
STATIONARY_REASON = "stationary: off and on within 5 km, not a case"
RECURRING_REASON = "all radar cells were recurring (fixed structure)"
SPEED_REASON = "no radar cell reachable at the class speed cap"
UNVERIFIED_REASON = "no scene time found; day lens only (unverified)"
SOURCES = [
    {
        "name": "Global Fishing Watch gap event",
        "dataset": "public-global-gaps-events:latest",
        "accessed": "2026-11-20",
    },
    {
        "name": "Global Fishing Watch SAR presence (unmatched)",
        "dataset": "public-global-sar-presence:latest",
        "accessed": "2026-11-20",
    },
    {
        "name": "Copernicus Data Space STAC",
        "dataset": "sentinel-1-grd",
        "accessed": "2026-11-20",
    },
]
CAVEATS = [
    "Speed cap is a class service-speed assumption, not measured.",
    "Radar detections carry a date only; the scene time is from Copernicus STAC when found.",
    "Radar misses vessels under about 25 m.",
    "Fixed structures are removed only where the same cell recurs on 3 or more days.",
]

# The scan's speed table, margin already included (tanker/cargo/carrier/other_non_fishing 17 kn,
# fishing 14, passenger 25, default 18; all x 1.15).
OLD_SPEED_KN = {
    "tanker": 19.55,
    "cargo": 19.55,
    "carrier": 19.55,
    "other_non_fishing": 19.55,
    "fishing": 16.1,
    "passenger": 28.75,
    "default": 20.7,
}


def old_speed_knots(vessel_type: str | None) -> float:
    """The scan's cap for a vessel class (unknown classes get the old default)."""
    return OLD_SPEED_KN.get((vessel_type or "").strip().lower(), OLD_SPEED_KN["default"])


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cache_key(url: str, params: dict, body: dict) -> str:
    material = json.dumps([url, params, body], sort_keys=True).encode()
    return hashlib.sha256(material).hexdigest()[:24]


def cached_post(cache: Path, url: str, params: dict, body: dict) -> dict:
    """The scan's cached response; a miss is an error, never a live request."""
    path = cache / f"{cache_key(url, params, body)}.json"
    if not path.exists():
        raise RuntimeError(f"cache miss: {url} {params} {cache_key(url, params, body)}")
    return json.loads(path.read_text())


def parse_t(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def fetch_gaps(cache: Path, start: str = START, end: str = END) -> list[dict]:
    out, offset = [], 0
    while offset is not None:
        page = cached_post(
            cache,
            f"{BASE}/events",
            {"limit": 200, "offset": offset},
            {"datasets": ["public-global-gaps-events:latest"], "startDate": start, "endDate": end},
        )
        out += page["entries"]
        offset = page.get("nextOffset")
    return out


def to_gap(entry: dict) -> dict:
    gap = entry["gap"]
    vessel = entry["vessel"]
    return {
        "id": entry["id"],
        "name": vessel.get("name"),
        "mmsi": vessel.get("ssvid"),
        "flag": vessel.get("flag"),
        "type": vessel.get("type"),
        "start": parse_t(entry["start"]),
        "end": parse_t(entry["end"]),
        "off": (gap["offPosition"]["lat"], gap["offPosition"]["lon"]),
        "on": (gap["onPosition"]["lat"], gap["onPosition"]["lon"]),
        "hours": float(gap["durationHours"]),
        "disabling": gap.get("intentionalDisabling"),
    }


def midpoint(gap: dict) -> tuple[float, float]:
    return ((gap["off"][0] + gap["on"][0]) / 2, (gap["off"][1] + gap["on"][1]) / 2)


def fetch_sar(cache: Path, bbox: tuple, d0: date, d1: date) -> list[dict]:
    """Unmatched SAR cells in bbox=(w,s,e,n) for days d0..d1 inclusive, split into <=31-day calls."""
    w, s, e, n = bbox
    poly = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    rows, a = [], d0
    while a <= d1:
        b = min(a + timedelta(days=30), d1)
        data = cached_post(
            cache,
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
        for entry in data.get("entries", []):
            for value in entry.values():
                if isinstance(value, list):
                    rows += value
        a = b + timedelta(days=1)
    return rows


def sar_rows(raw: list[dict]) -> list[tuple[float, float, date, int]]:
    return [(r["lat"], r["lon"], date.fromisoformat(r["date"][:10]), r.get("detections", 1)) for r in raw]


def footprint_hits(gap: dict, a: Fix, b: Fix, v_kn: float, sar: list[tuple]) -> list[int]:
    """Indices of SAR cells inside the gap's day span that pass the cheap footprint prefilter."""
    if not sar:
        return []
    slat = np.array([x[0] for x in sar])
    slon = np.array([x[1] for x in sar])
    sday = np.array([x[2].toordinal() for x in sar])
    reach = v_kn * KNOT_KM_H * gap["hours"]
    m = (sday >= gap["start"].date().toordinal()) & (sday <= gap["end"].date().toordinal())
    idx = np.where(m)[0]
    if not len(idx):
        return []
    da = distance_km_array(a.lat, a.lon, slat[idx], slon[idx])
    db = distance_km_array(slat[idx], slon[idx], b.lat, b.lon)
    return [int(i) for i in idx[da + db <= reach + 1e-3]]


def baseline_candidates(gap: dict, sar: list[tuple]) -> list:
    """The scan's candidate step, reproduced at the OLD speed caps."""
    a = Fix(gap["off"][0], gap["off"][1], gap["start"])
    b = Fix(gap["on"][0], gap["on"][1], gap["end"])
    v_old = old_speed_knots(gap["type"])
    idx = footprint_hits(gap, a, b, v_old, sar)
    if not idx:
        return []
    cells = [SarCell(sar[i][0], sar[i][1], sar[i][2]) for i in idx]
    return candidate_sightings(a, b, v_old, cells)


def ring(poly: Polygon) -> list[list[float]]:
    """GeoJSON exterior ring (closed) rounded to ~0.1 m; empty list for an empty polygon."""
    if poly.is_empty:
        return []
    return [[round(float(x), 6), round(float(y), 6)] for x, y in poly.exterior.coords]


def polygon_json(poly: Polygon) -> dict:
    coords = ring(poly)
    return {"type": "Polygon", "coordinates": [coords] if coords else []}


def speed_basis(vessel_type: str | None, v_kn: float) -> str:
    base = round(v_kn / MARGIN, 6)
    return f"{vessel_type} class service speed {base:g} kn x 1.15 (issue #28 assumption, not measured)"


def best_cell(cands) -> tuple[float, float] | None:
    if not cands:
        return None
    best = max(cands, key=lambda c: (c.slack, -c.cell.lat, -c.cell.lon))
    return (round(best.cell.lat, 2), round(best.cell.lon, 2))


def candidate_entry(
    cand,
    *,
    verdict: str,
    reason: str,
    scene_time: str | None = None,
    scene_source: str | None = None,
    lens_at_scene: dict | None = None,
    inside: bool | None = None,
    slack: float | None = None,
) -> dict:
    return {
        "pos": [round(cand.cell.lon, 6), round(cand.cell.lat, 6)],
        "cell_deg": cand.cell.size_deg,
        "day": cand.cell.day.isoformat(),
        "scene_time": scene_time,
        "scene_source": scene_source,
        "lens_at_scene": lens_at_scene,
        "inside": inside,
        "slack": round(cand.slack if slack is None else slack, 3),
        "verdict": verdict,
        "reason": reason,
    }


def case_detail(gap: dict, label: str, v_kn: float, status: str, candidates: list[dict]) -> dict:
    a = Fix(gap["off"][0], gap["off"][1], gap["start"])
    b = Fix(gap["on"][0], gap["on"][1], gap["end"])
    span = b.t - a.t
    straight = distance_km(a.lat, a.lon, b.lat, b.lon) / (gap["hours"] * KNOT_KM_H)
    frames = []
    for i in range(24):
        frac = i / 23
        t = a.t + span * frac
        frames.append({"t": iso_z(t), "frac": frac, "polygon": polygon_json(lens_polygon(a, b, v_kn, t))})
    return {
        "id": "gap:" + gap["id"],
        "vessel": {
            "name": gap["name"],
            "mmsi": gap["mmsi"],
            "flag": gap["flag"],
            "type": gap["type"],
        },
        "off": {"t": iso_z(gap["start"]), "pos": [gap["off"][1], gap["off"][0]]},
        "on": {"t": iso_z(gap["end"]), "pos": [gap["on"][1], gap["on"][0]]},
        "hours": round(gap["hours"], 1),
        "score": None,
        "band": None,
        "status": status,
        "n_candidates": len(candidates),
        "n_meetings": 0,
        "region_label": label,
        "speed_cap_kn": round(v_kn, 1),
        "speed_basis": speed_basis(gap["type"], v_kn),
        "straight_line_kn": round(straight, 1),
        "footprint": polygon_json(footprint_polygon(a, b, v_kn)),
        "lens_frames": frames,
        "candidates": candidates,
        "meetings": [],
        "score_factors": [],
        "sanctions": None,
        "sources": SOURCES,
        "caveats": CAVEATS,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-rank the region scan's SAR candidates (#28).")
    ap.add_argument("--root", default="/Users/kassemkanjo/Desktop/Hack Western")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    scan_cache = root / "data" / "cache" / "scan"
    stac_cache = root / "data" / "cache" / "stac"
    out_dir = root / "data" / "cases"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = fetch_gaps(scan_cache)
    gaps = [to_gap(e) for e in raw]
    n_all = len(gaps)
    gaps = [g for g in gaps if g["type"] != "gear" and 6 <= g["hours"] <= 96]
    print(f"events {n_all}, kept {len(gaps)} (non-gear, 6-96 h)")
    if len(gaps) != N_GAPS:
        raise RuntimeError(f"expected {N_GAPS} gaps after the filter, got {len(gaps)}")

    binned: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for g in gaps:
        la, lo = midpoint(g)
        binned[(int(np.floor(la / 5) * 5), int(np.floor(lo / 5) * 5))].append(g)
    top = sorted(binned.items(), key=lambda kv: -len(kv[1]))[:TOP_CELLS]

    win0, win1 = date.fromisoformat(START), date.fromisoformat(END)
    cell_sar: dict[tuple[int, int], list[tuple]] = {}
    cell_label: dict[tuple[int, int], str] = {}
    baseline: list[tuple[dict, list, tuple[int, int]]] = []
    for (la0, lo0), gl in top:
        bbox = (lo0 - PAD, la0 - PAD, lo0 + 5 + PAD, la0 + 5 + PAD)
        d0 = max(min(g["start"] for g in gl).date(), win0)
        d1 = min(max(g["end"] for g in gl).date(), win1)
        sar = sar_rows(fetch_sar(scan_cache, bbox, d0, d1))
        label = f"{la0}..{la0 + 5}N, {lo0}..{lo0 + 5}E"
        cell_sar[(la0, lo0)] = sar
        cell_label[(la0, lo0)] = label
        n_cell = 0
        for g in gl:
            cands = baseline_candidates(g, sar)
            if cands:
                n_cell += 1
                baseline.append((g, cands, (la0, lo0)))
        print(f"{label}: gaps {len(gl)}, with candidates {n_cell}, sar cells {len(sar)}")

    # Baseline reproduction: the old speed table must reproduce the scan's own numbers.
    scan = json.loads((out_dir / "scan_results.json").read_text())
    want_gaps = sum(c["gaps_with_candidates"] for c in scan["cells"])
    want_cands = sum(c["total_candidates"] for c in scan["cells"])
    got_cands = sum(len(c) for _, c, _ in baseline)
    print(f"baseline: gaps with candidates {len(baseline)} (scan {want_gaps}), candidates {got_cands}")
    if len(baseline) != want_gaps or got_cands != want_cands:
        raise RuntimeError(
            f"baseline mismatch: gaps {len(baseline)} vs {want_gaps}, candidates {got_cands} vs {want_cands}"
        )

    stage_a = {"gaps_removed": 0, "candidates_removed": 0}
    stage_b = {"candidates_removed": 0, "gaps_with_zero_left": 0}
    stage_c = {"candidates_left": 0, "gaps_with_at_least_one": 0, "gaps_best_cell_changed": 0}

    # Stage B is per CELL: recurring cells come from every unmatched SAR row fetched for it.
    recurring = {
        key: recurring_cells([(round(lat, 2), round(lon, 2), day) for lat, lon, day, _ in sar])
        for key, sar in cell_sar.items()
    }

    made: list[tuple[dict, str | None]] = []  # (CaseDetail, rule-out reason for the counts table)
    pending: list[dict] = []  # survivors of stages A-C, awaiting scene times

    for g, base_cands, cell_key in baseline:
        label = cell_label[cell_key]
        a = Fix(g["off"][0], g["off"][1], g["start"])
        b = Fix(g["on"][0], g["on"][1], g["end"])
        v_old = old_speed_knots(g["type"])

        # Stage A: a ship that never really moved is not a case.
        if is_stationary((a, b, v_old), km=STATIONARY_KM):
            stage_a["gaps_removed"] += 1
            stage_a["candidates_removed"] += len(base_cands)
            entries = [candidate_entry(c, verdict="ruled_out", reason=STATIONARY_REASON) for c in base_cands]
            made.append((case_detail(g, label, v_old, "ruled_out", entries), STATIONARY_REASON))
            continue

        # Stage B: drop radar cells that recur on 3+ days (platforms, moorings).
        rec = recurring[cell_key]
        remaining = [c for c in base_cands if (round(c.cell.lat, 2), round(c.cell.lon, 2)) not in rec]
        stage_b["candidates_removed"] += len(base_cands) - len(remaining)
        if not remaining:
            stage_b["gaps_with_zero_left"] += 1

        # Stage C: recompute what is reachable at the class service-speed cap.
        v_new = max_speed_knots(g["type"])
        best_before = best_cell(base_cands)
        kept = [
            c
            for c in remaining
            if distance_km(a.lat, a.lon, c.cell.lat, c.cell.lon)
            + distance_km(c.cell.lat, c.cell.lon, b.lat, b.lon)
            <= v_new * KNOT_KM_H * g["hours"] + 1e-3
        ]
        cells = [SarCell(c.cell.lat, c.cell.lon, c.cell.day) for c in kept]
        survivors = candidate_sightings(a, b, v_new, cells) if cells else []
        stage_c["candidates_left"] += len(survivors)
        if survivors:
            stage_c["gaps_with_at_least_one"] += 1
            if best_cell(survivors) != best_before:
                stage_c["gaps_best_cell_changed"] += 1
        if not survivors:
            reason = RECURRING_REASON if not remaining else SPEED_REASON
            made.append((case_detail(g, label, v_new, "ruled_out", []), reason))
            continue
        pending.append({"gap": g, "cands": survivors, "cell": cell_key, "v": v_new})

    print(f"stage A stationary: {stage_a}")
    print(f"stage B recurring: {stage_b}")
    print(f"stage C speed caps: {stage_c}")

    # Stage D: one scene-time lookup per unique (lat, lon, day), cached in this run.
    wanted = {(c.cell.lat, c.cell.lon, c.cell.day) for item in pending for c in item["cands"]}
    keys = sorted(wanted, key=lambda k: (k[0], k[1], k[2]))

    def lookup(key: tuple[float, float, date]):
        lat, lon, day = key
        try:
            return key, scene_times(lat, lon, day, cache_dir=stac_cache)
        except Exception as exc:  # noqa: BLE001 - any STAC failure means "unverified"
            return key, exc

    times_by_key: dict[tuple[float, float, date], list[datetime]] = {}
    stac_errors = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, result in pool.map(lookup, keys):
            if isinstance(result, Exception):
                stac_errors += 1
                times_by_key[key] = []
                print(f"stac error {key}: {result!r}")
            else:
                times_by_key[key] = result

    stage_d = {
        "unique_cell_days": len(keys),
        "candidates_with_scene": 0,
        "candidates_without_scene": 0,
        "stac_errors": stac_errors,
    }
    stage_e = {"inside": 0, "outside": 0}

    # Stage E-F: judge each surviving candidate at its best scene time, then the gap as a whole.
    for item in pending:
        g, cands, v_new = item["gap"], item["cands"], item["v"]
        a = Fix(g["off"][0], g["off"][1], g["start"])
        b = Fix(g["on"][0], g["on"][1], g["end"])
        entries = []
        for cand in cands:
            times = times_by_key.get((cand.cell.lat, cand.cell.lon, cand.cell.day), [])
            if not times:
                stage_d["candidates_without_scene"] += 1
                entries.append(
                    candidate_entry(cand, verdict="candidate", reason=UNVERIFIED_REASON, slack=cand.slack)
                )
                continue
            stage_d["candidates_with_scene"] += 1
            scene, (inside, slack, reason) = max(
                ((t, verdict_at_scene(a, b, v_new, cand.cell, t)) for t in times),
                key=lambda pair: (pair[1][0], pair[1][1]),
            )
            stage_e["inside" if inside else "outside"] += 1
            entries.append(
                candidate_entry(
                    cand,
                    verdict="candidate" if inside else "ruled_out",
                    reason=reason,
                    scene_time=iso_z(scene),
                    scene_source="copernicus-stac",
                    lens_at_scene=polygon_json(lens_polygon(a, b, v_new, scene)),
                    inside=inside,
                    slack=slack,
                )
            )
        if any(e["verdict"] == "candidate" for e in entries):
            made.append((case_detail(g, cell_label[item["cell"]], v_new, "candidate", entries), None))
        else:
            top_entry = max(entries, key=lambda e: e["slack"])
            reason = "no candidate survives the scene-time check: " + top_entry["reason"]
            made.append((case_detail(g, cell_label[item["cell"]], v_new, "ruled_out", entries), reason))

    print(f"stage D scene times: {stage_d}")
    print(f"stage E scene verdicts: {stage_e}")

    # Ranking: survivors first, verified (real scene time) before unverified, then best slack.
    def rank(case: dict):
        verdicts = [c for c in case["candidates"] if c["verdict"] == "candidate"]
        best = max((c["slack"] for c in verdicts), default=0.0)
        verified = any(c["scene_source"] is not None for c in case["candidates"])
        return (0 if case["status"] == "candidate" else 1, not verified, -best, -case["hours"], case["id"])

    made.sort(key=lambda pair: rank(pair[0]))
    cases = [case for case, _ in made]
    reasons = Counter(reason for _, reason in made if reason is not None)

    totals = {
        "gaps_scanned": len(gaps),
        "with_candidates": len(baseline),
        "ruled_out": sum(1 for c in cases if c["status"] == "ruled_out"),
        "meetings": 0,
    }
    payload = {"generated_at": iso_z(datetime.now(UTC)), "totals": totals, "cases": cases}
    (out_dir / "cases_v2.json").write_text(json.dumps(payload, indent=1))

    counts = {
        "baseline": {"gaps_with_candidates": len(baseline), "total_candidates": got_cands},
        "stage_a_stationary": stage_a,
        "stage_b_recurring": stage_b,
        "stage_c_speed_caps": stage_c,
        "stage_d_scene_times": stage_d,
        "stage_e_scene_verdicts": stage_e,
        "final": {
            "candidate": sum(1 for c in cases if c["status"] == "candidate"),
            "ruled_out": totals["ruled_out"],
            "ruled_out_by_reason": dict(reasons.most_common()),
        },
        "speed_table_kn": {
            "old": OLD_SPEED_KN,
            "new": {key: round(max_speed_knots(key), 2) for key in [*OLD_SPEED_KN, "bulk"]},
        },
    }
    (out_dir / "rerank_counts.json").write_text(json.dumps(counts, indent=1))
    print(json.dumps(counts, indent=2))

    # Fixture: the best case in the ranking, with a note when nothing survived.
    fixture_dir = ROOT / "tests" / "fixtures" / "api"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture = cases[0]
    if fixture["status"] != "candidate":
        fixture = {**fixture, "fixture_note": "best ruled-out case; no survivor"}
    (fixture_dir / "case.json").write_text(json.dumps(fixture, indent=2))
    print(f"wrote {out_dir / 'cases_v2.json'}, {out_dir / 'rerank_counts.json'}, {fixture_dir / 'case.json'}")


if __name__ == "__main__":
    main()
