# ruff: noqa: E501
"""Validation (#27): does "unmatched radar inside a dark ship's prism" beat chance, and does the prism
contain a ship's true position?

    ~/.local/bin/uv run python scripts/validate.py [--k 50] [--env /abs/path/.env]

Test 1 (null shuffle): recount "gap has >=1 unmatched SAR cell in its day-lens prism" with each gap's prism
moved in time by a random 7-21 days (same place, same geometry, SAR unchanged).
Test 2 (ground-truth recall): the GFW API cannot attribute radar detections to vessels right now (see the
probes below), so we use the next-best truth: a vessel's own intermediate GFW event (port visit, loitering,
encounter) as the "true position" inside a synthetic gap built from the events before and after it.

All API responses are cached under data/cache/scan/ (shared with scan_regions.py), so reruns make no
calls. The token is only sent in the Authorization header. Writes data/validation/results.json and
docs/validation.md.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from shapely.errors import GEOSException
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import scan_regions as sr  # noqa: E402  (shares the cache, gap fetch and SAR fetch)

from darkfleet.core.geo import KNOT_KM_H, Fix, distance_km, distance_km_array  # noqa: E402
from darkfleet.core.match import SarCell, candidate_sightings  # noqa: E402
from darkfleet.core.prism import (  # noqa: E402
    day_lens_polygon,
    footprint_polygon,
    in_lens,
    unwrap_lon,
)
from darkfleet.core.speeds import max_speed_knots  # noqa: E402
from darkfleet.core.validation import (  # noqa: E402
    allowed_shifts,
    draw_shifts,
    interpret_ratio,
    null_summary,
    offset_point,
    paired_ratio_ci,
    random_offsets,
    wilson_interval,
)

OUT_JSON = ROOT / "data" / "validation" / "results.json"
OUT_MD = ROOT / "docs" / "validation.md"
SHIFT_LO, SHIFT_HI = 7, 21
S_MIN = date(2026, 7, 25)  # scan window start (15 Aug) minus the largest shift
S_MAX = date(2026, 11, 15)  # last day the scan covered; later radar days are not fetched
STATIONARY_KM = 5.0  # plan item 3: off ~ on within 5 km is a parked ship, not a case
FIXED_DAYS = 3  # plan item 3: a radar cell seen on >= 3 days is a fixed structure
EVENT_DATASETS = [
    "public-global-encounters-events:latest",
    "public-global-loitering-events:latest",
    "public-global-port-visits-events:latest",
]

# ---- shared state for forked workers -------------------------------------------------------------
_G: dict = {}


def _init_worker(gaps: list, cells: dict) -> None:
    _G["gaps"], _G["cells"] = gaps, cells


def _safe_candidates(a: Fix, b: Fix, v: float, cells: list) -> list:
    """candidate_sightings, retried with other time steps if GEOS rejects a degenerate lens union
    (seen on a few prisms; core/prism.py is out of scope here). Counted in _G['fallbacks']."""
    for step in (30, 29, 37, 41):
        try:
            if step != 30:
                _G["fallbacks"] = _G.get("fallbacks", 0) + 1
            return candidate_sightings(a, b, v, cells, step_minutes=step)
        except GEOSException:
            continue
    _G["failures"] = _G.get("failures", 0) + 1
    return []


def _flags_for_gap(i: int) -> tuple[int, list[tuple[int, int, int]], tuple[int, int]]:
    """For gap i: (index, [(shift, any_candidate, any_non_fixed_candidate)]) for shift 0 and each allowed shift."""
    g = _G["gaps"][i]
    cell = _G["cells"][g["cell_key"]]
    slat, slon, sday = cell["lat"], cell["lon"], cell["day"]
    fixed = cell["fixed"]
    v = max_speed_knots(g["type"])
    reach = v * KNOT_KM_H * g["hours"]
    out = []
    for d in [0, *g["allowed"].tolist()]:
        s0 = g["start"].date().toordinal() + d
        s1 = g["end"].date().toordinal() + d
        idx = np.where((sday >= s0) & (sday <= s1))[0]
        if len(idx):
            da = distance_km_array(g["off"][0], g["off"][1], slat[idx], slon[idx])
            db = distance_km_array(slat[idx], slon[idx], g["on"][0], g["on"][1])
            idx = idx[da + db <= reach + 1e-3]
        any_c = any_nf = 0
        if len(idx):
            dt = timedelta(days=d)
            a = Fix(g["off"][0], g["off"][1], g["start"] + dt)
            b = Fix(g["on"][0], g["on"][1], g["end"] + dt)
            cells = [SarCell(float(slat[j]), float(slon[j]), date.fromordinal(int(sday[j]))) for j in idx]
            cands = _safe_candidates(a, b, v, cells)
            any_c = int(bool(cands))
            any_nf = int(any((c.cell.lat, c.cell.lon) not in fixed for c in cands))
        out.append((d, any_c, any_nf))
    return i, out, (_G.pop("fallbacks", 0), _G.pop("failures", 0))


def run_null(cli, gaps_all, k: int, seed: int) -> dict:
    """Test 1. Fetches SAR once per 5x5 cell for the whole window, then computes every (gap, shift) flag."""
    cells_bin: dict = defaultdict(list)
    for g in gaps_all:
        la, lo = sr.midpoint(g)
        cells_bin[(int(np.floor(la / 5) * 5), int(np.floor(lo / 5) * 5))].append(g)
    top = sorted(cells_bin.items(), key=lambda kv: -len(kv[1]))[:15]  # same 15 cells as the region scan
    sar_by_cell, gaps = {}, []
    for (la0, lo0), gl in top:
        bbox = (lo0 - 1.0, la0 - 1.0, lo0 + 6.0, la0 + 6.0)
        rows = sr.fetch_sar(cli, bbox, S_MIN, S_MAX)
        lat = np.array([r["lat"] for r in rows], float)
        lon = np.array([r["lon"] for r in rows], float)
        day = np.array([date.fromisoformat(r["date"][:10]).toordinal() for r in rows], int)
        days_at = defaultdict(set)
        for a, b, dd in zip(lat, lon, day, strict=True):
            days_at[(float(a), float(b))].add(int(dd))
        fixed = {p for p, ds in days_at.items() if len(ds) >= FIXED_DAYS}
        key = (la0, lo0)
        sar_by_cell[key] = {"lat": lat, "lon": lon, "day": day, "fixed": fixed}
        for g in gl:
            g = dict(g, cell_key=key)
            g["off_on_km"] = distance_km(g["off"][0], g["off"][1], g["on"][0], g["on"][1])
            dmin = (S_MIN - g["start"].date()).days
            dmax = (S_MAX - g["end"].date()).days
            g["allowed"] = allowed_shifts(SHIFT_LO, SHIFT_HI, dmin, dmax)
            gaps.append(g)
        print(f"  cell {key}: gaps {len(gl)}, SAR cells {len(rows)}, fixed coords {len(fixed)}")
    # spawn, not fork: forking after pyproj/GEOS are loaded hung on macOS
    with mp.get_context("spawn").Pool(
        min(8, os.cpu_count() or 2), initializer=_init_worker, initargs=(gaps, sar_by_cell)
    ) as pool:
        raw_res = list(pool.imap_unordered(_flags_for_gap, range(len(gaps)), chunksize=4))
    res = {i: rows for i, rows, _ in raw_res}
    n_fallback = sum(c[0] for _, _, c in raw_res)
    n_fail = sum(c[1] for _, _, c in raw_res)
    n = len(gaps)
    table = {"all": np.full((n, 2 * SHIFT_HI + 1), -1, int), "nf": np.full((n, 2 * SHIFT_HI + 1), -1, int)}
    for i, rows in res.items():
        for d, a, nf in rows:
            table["all"][i, d + SHIFT_HI] = a
            table["nf"][i, d + SHIFT_HI] = nf
    real_scan_total = int(table["all"][:, SHIFT_HI].sum())

    rng = np.random.default_rng(seed)
    drawn = draw_shifts(rng, [g["allowed"] for g in gaps], k)
    has_shift = np.array([len(g["allowed"]) > 0 for g in gaps])
    moving = np.array([g["off_on_km"] >= STATIONARY_KM for g in gaps])
    variants = {}
    for name, label, key, sel in (
        (
            "primary",
            "All kept gaps, all unmatched radar cells (as specified)",
            "all",
            has_shift,
        ),
        (
            "filtered",
            f"Moving gaps only (off-on >= {STATIONARY_KM:g} km) and radar cells seen on < {FIXED_DAYS} days "
            "(plan item 3 filters, declared in advance)",
            "nf",
            has_shift & moving,
        ),
    ):
        t = table[key][sel]
        real = t[:, SHIFT_HI]
        shuf = np.take_along_axis(t, drawn[sel] + SHIFT_HI, axis=1)
        assert (shuf >= 0).all() and (real >= 0).all()
        summ = null_summary(int(real.sum()), shuf.sum(axis=0))
        ratio, lo, hi = paired_ratio_ci(real, shuf, np.random.default_rng(seed + 1))
        variants[name] = {
            "label": label,
            "n_gaps": int(sel.sum()),
            **summ,
            "ratio": None if np.isnan(ratio) else ratio,
            "ratio_lo": None if np.isnan(lo) else lo,
            "ratio_hi": None if np.isnan(hi) else hi,
            "interpretation": interpret_ratio(ratio, lo, hi),
        }
        print(name, {a: b for a, b in variants[name].items() if a != "label"})
    by_cell = []
    for key_ in sorted({g["cell_key"] for g in gaps}, key=lambda c: -sum(g["cell_key"] == c for g in gaps)):
        sel = np.array([g["cell_key"] == key_ for g in gaps]) & has_shift
        t = table["all"][sel]
        real = t[:, SHIFT_HI]
        shuf = np.take_along_axis(t, drawn[sel] + SHIFT_HI, axis=1)
        by_cell.append(
            {
                "cell": f"{key_[0]}..{key_[0] + 5}N, {key_[1]}..{key_[1] + 5}E",
                "n_gaps": int(sel.sum()),
                "real": int(real.sum()),
                "shuffled_mean": float(shuf.sum(axis=0).mean()) if sel.any() else 0.0,
            }
        )
    return {
        "variants": variants,
        "by_cell": by_cell,
        "n_gaps_total": n,
        "prism_time_step_fallbacks": n_fallback,
        "prism_failures_counted_as_no_hit": n_fail,
        "n_gaps_no_valid_shift": int((~has_shift).sum()),
        "real_count_all_gaps_unpaired": real_scan_total,
        "spatial_shuffle": {
            "done": False,
            "reason": "Skipped: no land mask in the project and a 50-150 km spatial shift would need radar "
            "re-fetched around every shifted prism (the cached radar covers each 5x5 degree cell plus 1 degree "
            "only), and without a land mask shifted prisms that land on shore would bias the shuffled count "
            "downward",
        },
        "notes": [
            f"{n} gaps in the 15 busiest 5x5 degree cells (the ones the region scan fetched radar for); the "
            "other 4,286 kept gaps have no radar fetched and are not in this test.",
            f"Each gap's prism is shifted by a random whole number of days with 7 <= |shift| <= 21, drawn "
            f"uniformly from the shifts that keep the shifted gap inside {S_MIN} to {S_MAX} (radar days we "
            f"fetched). {k} shuffles; {int((~has_shift).sum())} gaps with no valid shift are left out.",
            "Shifts are whole days, so the shifted prism is the same shape and the same time of day; only "
            "which radar overpasses fall in it changes.",
        ],
    }


# ---- Test 2 ---------------------------------------------------------------------------------------


def _pt(e: dict) -> tuple[float, float]:
    return float(e["position"]["lat"]), float(e["position"]["lon"])


def _evt(e: dict) -> dict:
    return {
        "type": e["type"],
        "vessel": e["vessel"]["id"],
        "vtype": e["vessel"].get("type"),
        "start": sr.parse_t(e["start"]),
        "end": sr.parse_t(e["end"]),
        "pos": _pt(e),
    }


def fetch_events(cli, vessel_ids: list[str], start: str, end: str) -> list[dict]:
    out, offset = [], 0
    while offset is not None:
        d = sr.cached_post(
            cli,
            f"{sr.BASE}/events",
            {"limit": 200, "offset": offset},
            {"datasets": EVENT_DATASETS, "vessels": vessel_ids, "startDate": start, "endDate": end},
        )
        out += d["entries"]
        offset = d.get("nextOffset")
        if offset is not None and offset >= 600:  # cap pages per batch (3 pages)
            break
    return out


def build_cases(events: list[dict], per_vessel: int = 3) -> list[dict]:
    """Triples A, C, B of one vessel's non-overlapping events; A end -> B start is the synthetic gap and
    C's position at C's start is the known truth. Filters (declared in advance): gap 6-96 h, C at least 10 km
    from A and from B, A and B at least 20 km apart, events shorter than 30 days."""
    by_v = defaultdict(list)
    for e in events:
        ev = _evt(e)
        if ev["end"] >= ev["start"] and (ev["end"] - ev["start"]) < timedelta(days=30):
            by_v[ev["vessel"]].append(ev)
    cases = []
    for vid, evs in by_v.items():
        evs.sort(key=lambda x: (x["start"], x["end"]))
        chain = []
        for ev in evs:
            if not chain or ev["start"] >= chain[-1]["end"]:
                chain.append(ev)
        got = 0
        for i in range(0, len(chain) - 2, 3):
            a, c, b = chain[i : i + 3]
            hours = (b["start"] - a["end"]).total_seconds() / 3600
            if not 6 <= hours <= 96:
                continue
            if distance_km(*a["pos"], *b["pos"]) < 20:
                continue
            if distance_km(*a["pos"], *c["pos"]) < 10 or distance_km(*c["pos"], *b["pos"]) < 10:
                continue
            cases.append({"vessel": vid, "vtype": a["vtype"], "a": a, "b": b, "c": c, "hours": hours})
            got += 1
            if got >= per_vessel:
                break
    return cases


def _hits(poly, lat: float, lon: float) -> bool:
    h = 0.005  # same 0.01 degree radar cell as core.match
    if poly.is_empty:
        return False
    # polygon vertices are unwrapped around their own centre; test the cell in that frame
    lon = float(unwrap_lon(lon, float(poly.centroid.x)))
    return poly.intersects(box(lon - h, lat - h, lon + h, lat + h))


def score_cases(cases: list[dict], seed: int, n_offsets: int = 20) -> dict:
    rng = np.random.default_rng(seed)
    rows = []
    skipped = 0
    for cs in cases:
        a = Fix(*cs["a"]["pos"], cs["a"]["end"])
        b = Fix(*cs["b"]["pos"], cs["b"]["start"])
        v = max_speed_knots(cs["vtype"])
        lat, lon = cs["c"]["pos"]
        t_true = cs["c"]["start"]
        day = t_true.date()
        foot = footprint_polygon(a, b, v)
        dl = None
        for step in (30, 29, 37, 41):  # GEOS can reject a degenerate lens union; retry other steps
            try:
                dl = day_lens_polygon(a, b, v, day, step_minutes=step)
                break
            except GEOSException:
                continue
        if dl is None:
            skipped += 1
            continue
        dist, brg = random_offsets(rng, n_offsets)
        chance = 0
        for dd, bb in zip(dist, brg, strict=True):
            olat, olon = offset_point(lat, lon, float(dd), float(bb))
            chance += int(_hits(dl, olat, olon))
        rows.append(
            {
                "vtype": cs["vtype"],
                "hours": cs["hours"],
                "footprint": _hits(foot, lat, lon),
                "day_lens": _hits(dl, lat, lon),
                "instant_lens": bool(in_lens(a, b, v, lat, lon, t_true)),
                "chance_hits": chance,
                "chance_trials": n_offsets,
            }
        )
    n = len(rows)

    def rate(label, k, nn):
        lo, hi = wilson_interval(k, nn)
        return {"label": label, "k": k, "n": nn, "rate": k / nn if nn else float("nan"), "lo": lo, "hi": hi}

    rates = {
        "footprint": rate(
            "True position inside the whole-gap footprint", sum(r["footprint"] for r in rows), n
        ),
        "day_lens": rate(
            "Inside the day-lens (the radar-cell test we use)", sum(r["day_lens"] for r in rows), n
        ),
        "instant_lens": rate("Inside the lens at the exact instant", sum(r["instant_lens"] for r in rows), n),
        "chance_day_lens": rate(
            "Chance: same test with the true position moved 50-150 km in a random direction",
            sum(r["chance_hits"] for r in rows),
            sum(r["chance_trials"] for r in rows),
        ),
    }
    by_type = defaultdict(lambda: {"n": 0, "day_lens_k": 0})
    for r in rows:
        by_type[r["vtype"] or "unknown"]["n"] += 1
        by_type[r["vtype"] or "unknown"]["day_lens_k"] += int(r["day_lens"])
    return {
        "n_cases": n,
        "n_skipped_geometry_error": skipped,
        "n_vessels": len({c["vessel"] for c in cases}),
        "median_gap_hours": float(np.median([r["hours"] for r in rows])) if rows else float("nan"),
        "rates": rates,
        "by_type": dict(by_type),
    }


PROBES = [
    {
        "request": "POST /v3/4wings/report public-global-sar-presence:latest, filters[0]=matched='true', group-by=VESSEL_ID",
        "result": "404: 'This feature is temporarily unavailable because of infrastructure constraints and an upstream data dependency issue.'",
    },
    {"request": "same, group-by=MMSI", "result": "404, same message"},
    {
        "request": "same, group-by=DETECT_ID",
        "result": "422: option not valid for dataset public-global-sar-presence:v5.0",
    },
    {
        "request": "same, no group-by (matched='true')",
        "result": "200, rows carry date, lat, lon, detections and a vesselIDs COUNT (1), not an identity",
    },
    {
        "request": "same, group-by=FLAG or GEARTYPE",
        "result": "200: adds the vessel's flag or class to each matched detection, still no identity",
    },
]
NEXT_BEST = (
    "The API will not say which vessel a matched radar detection belongs to, so the real recall of "
    "'prism plus radar finds the true ship' cannot be measured yet. Next-best truth used here: a vessel's own "
    "GFW event between two others (port visit, loitering, encounter) as the true position inside a synthetic "
    "gap. That tests the physics (speed cap, prism geometry), not radar visibility. Once 4Wings vessel "
    "attribution returns, rerun with matched detections as truth; or use a licensed AIS archive to hide a "
    "vessel's track and ask whether its radar blob falls in the prism."
)


def run_recall(cli, gaps_all, target: int, max_requests: int, seed: int) -> dict:
    vids = sorted({g["vessel_id"] for g in gaps_all if g.get("vessel_id")})
    random.Random(seed).shuffle(vids)
    events, cases, used = [], [], 0
    start_req = sr.REQUESTS
    for i in range(0, len(vids), 10):
        if len(cases) >= target or sr.REQUESTS - start_req >= max_requests:
            break
        events += fetch_events(cli, vids[i : i + 10], "2026-08-15", "2026-11-15")
        used = i + 10
        cases = build_cases(events)
    out = score_cases(cases, seed)
    out["vessels_queried"] = min(used, len(vids))
    out["api"] = {"vessel_attribution_available": False, "probes": PROBES, "next_best": NEXT_BEST}
    return out


# ---- report renderer (DeepSeek piece) ---------------------------------------------------------

NA = "n/a"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any, default: str = NA) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _count(value: Any, decimals: int = 0) -> str:
    number = _number(value)
    if number is None:
        return NA
    if decimals > 0:
        return f"{number:,.{decimals}f}"
    return f"{number:,.0f}"


def _rate(value: Any) -> str:
    number = _number(value)
    if number is None:
        return NA
    return f"{number * 100:.1f}%"


def _ratio(value: Any) -> str:
    number = _number(value)
    if number is None:
        return NA
    return f"{number:.2f}"


def _p_value(value: Any) -> str:
    number = _number(value)
    if number is None:
        return NA
    if number < 0.001:
        return "<0.001"
    return f"{number:.3f}"


def _interval(lo: Any, hi: Any, formatter: Callable[[Any], str]) -> str:
    low = formatter(lo)
    high = formatter(hi)
    if low == NA or high == NA:
        return NA
    return f"{low} to {high}"


def _with_interval(value: Any, lo: Any, hi: Any, formatter: Callable[[Any], str]) -> str:
    center = formatter(value)
    spread = _interval(lo, hi, formatter)
    if center == NA and spread == NA:
        return NA
    return f"{center} ({spread})"


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    width = len(headers)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    if not rows:
        rows = [[NA] * width]
    for row in rows:
        cells = [_escape_cell(str(cell)) for cell in row[:width]]
        cells.extend([""] * (width - len(cells)))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _bullets(items: Any) -> list[str]:
    values = _sequence(items)
    if not values:
        return [f"- {NA}"]
    lines: list[str] = []
    for item in values:
        parts = _text(item).splitlines()
        lines.append(f"- {parts[0]}")
        lines.extend(f"  {part}" for part in parts[1:])
    return lines


def _variant_label(name: str, variant: dict[str, Any]) -> str:
    label = _text(variant.get("label"))
    return _text(name) if label == NA else label


def _rate_rows(rates: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name, raw in rates.items():
        entry = _mapping(raw)
        rows.append(
            [
                _variant_label(str(name), entry),
                f"{_count(entry.get('k'))} / {_count(entry.get('n'))}",
                _rate(entry.get("rate")),
                _interval(entry.get("lo"), entry.get("hi"), _rate),
            ]
        )
    return rows


def render_report(r: dict) -> str:
    """Render docs/validation.md from the results dict (schema below).

    Plain black-and-white markdown: no emojis, no HTML, no coloured text and no bold-heavy styling.
    Counts use thousands separators, rates are percent with one decimal and ratios have two decimals.
    """
    generated = _text(r.get("generated"))
    live_requests = _count(r.get("requests_live"))

    null_test = _mapping(r.get("null_test"))
    variants = _mapping(null_test.get("variants"))
    spatial = _mapping(null_test.get("spatial_shuffle"))
    notes = null_test.get("notes")

    recall = _mapping(r.get("recall_test"))
    api = _mapping(recall.get("api"))
    rates = _mapping(recall.get("rates"))
    by_type = _mapping(recall.get("by_type"))

    lines: list[str] = [
        "# Validation: does radar in a dark ship's prism beat chance?",
        "",
        f"Generated: {generated}",
        f"Live API requests: {live_requests}",
        "The report compares observed dark-gap matches with shuffled null runs and ground-truth recall.",
        "",
        "## Headline",
        "",
    ]

    if variants:
        for name, raw in variants.items():
            variant = _mapping(raw)
            label = _variant_label(str(name), variant)
            lines.extend(
                [
                    *_table(
                        ["Measure", "Value"],
                        [
                            ["Variant", label],
                            ["Real matches", _count(variant.get("real"))],
                            [
                                "Shuffled mean (95% range)",
                                _with_interval(
                                    variant.get("shuffled_mean"),
                                    variant.get("null_lo"),
                                    variant.get("null_hi"),
                                    lambda value: _count(value, 1),
                                ),
                            ],
                            [
                                "Ratio (95% interval)",
                                _with_interval(
                                    variant.get("ratio"),
                                    variant.get("ratio_lo"),
                                    variant.get("ratio_hi"),
                                    _ratio,
                                ),
                            ],
                            ["p-value", _p_value(variant.get("p_value"))],
                            ["Interpretation", _text(variant.get("interpretation"))],
                        ],
                    ),
                    "",
                ]
            )
    else:
        lines.extend(["No null variants reported.", ""])

    lines.extend(
        [
            "Recall rates:",
            "",
            *_table(
                ["Measure", "k / n", "Rate", "95% Wilson interval"],
                _rate_rows(rates),
            ),
            "",
            "## Method in plain words",
            "",
            *_bullets(r.get("method")),
            "",
            "## Test 1: the null shuffle",
            "",
        ]
    )

    if variants:
        for name, raw in variants.items():
            variant = _mapping(raw)
            label = _variant_label(str(name), variant)
            lines.extend(
                [
                    *_table(
                        ["Measure", "Value"],
                        [
                            ["Variant", label],
                            ["Dark gaps tested", _count(variant.get("n_gaps"))],
                            ["Real matches", _count(variant.get("real"))],
                            ["Shuffles", _count(variant.get("k"))],
                            ["Shuffled mean", _count(variant.get("shuffled_mean"), 1)],
                            ["Shuffled standard deviation", _count(variant.get("shuffled_sd"), 1)],
                            [
                                "95% null range",
                                _interval(
                                    variant.get("null_lo"),
                                    variant.get("null_hi"),
                                    lambda value: _count(value, 1),
                                ),
                            ],
                            [
                                "Ratio (95% interval)",
                                _with_interval(
                                    variant.get("ratio"),
                                    variant.get("ratio_lo"),
                                    variant.get("ratio_hi"),
                                    _ratio,
                                ),
                            ],
                            ["p-value", _p_value(variant.get("p_value"))],
                            ["Interpretation", _text(variant.get("interpretation"))],
                        ],
                    ),
                    "",
                ]
            )
    else:
        lines.extend(["No variant details reported.", ""])

    done = spatial.get("done")
    reason = _text(spatial.get("reason"))
    if done is True:
        spatial_line = "Spatial shuffle: done."
    elif done is False:
        spatial_line = f"Spatial shuffle: not done. Reason: {reason}."
    else:
        spatial_line = "Spatial shuffle: n/a."
    if done is True and reason != NA:
        spatial_line = f"Spatial shuffle: done. {reason}"

    lines.extend([spatial_line, ""])
    lines.extend(_bullets(notes))
    lines.extend(["", "## Test 2: ground-truth recall", ""])

    probes = _sequence(api.get("probes"))
    lines.extend(["API probes:", ""])
    lines.extend(
        _table(
            ["Request", "Result"],
            [
                [
                    _text(_mapping(probe).get("request")),
                    _text(_mapping(probe).get("result")),
                ]
                for probe in probes
            ],
        )
    )

    available = api.get("vessel_attribution_available")
    if available is True:
        available_text = "yes"
    elif available is False:
        available_text = "no"
    else:
        available_text = NA

    lines.extend(
        [
            "",
            f"Vessel attribution available: {available_text}",
            f"Next-best alternative: {_text(api.get('next_best'))}",
            "",
            *_table(
                ["Measure", "Value"],
                [
                    ["Cases", _count(recall.get("n_cases"))],
                    ["Vessels", _count(recall.get("n_vessels"))],
                    ["Median gap (hours)", _count(recall.get("median_gap_hours"), 1)],
                ],
            ),
            "",
            "Recall rates:",
            "",
            *_table(
                ["Measure", "k / n", "Rate", "95% Wilson interval"],
                _rate_rows(rates),
            ),
            "",
            "Recall by gap type:",
            "",
            *_table(
                ["Gap type", "n", "Day-lens k"],
                [
                    [
                        _text(name),
                        _count(_mapping(entry).get("n")),
                        _count(_mapping(entry).get("day_lens_k")),
                    ]
                    for name, entry in by_type.items()
                ],
            ),
            "",
            "## Caveats",
            "",
            *_bullets(r.get("caveats")),
            "",
            "## What would change the conclusion",
            "",
            *_bullets(r.get("would_change")),
            "",
            "Data: Global Fishing Watch (CC BY-NC 4.0). Powered by Global Fishing Watch.",
        ]
    )

    return "\n".join(lines) + "\n"


# ---- main -----------------------------------------------------------------------------------------

METHOD = [
    "Take the AIS gaps the region scan kept (5,624 non-gear gaps of 6 to 96 hours; 1,338 of them sit in the 15 cells where radar was fetched and enter Test 1). For each gap the 'prism' is "
    "every place the ship could have reached given its last and next known position, with a class speed limit.",
    "A gap counts as a hit if at least one unmatched radar cell on a day of the gap lies inside that prism "
    "(radar cells carry a date, not a time, so we use the whole day's prism).",
    "Test 1 moves each gap's prism in time by 7 to 21 days (same place, same shape, same radar data) and recounts. "
    "If the real count is no higher than these shifted counts, the radar cells we found are what you would see in "
    "that water on any week, not a signal about the ship.",
    "Test 2 checks the prism itself: where a ship's position is known from GFW events between two other events, "
    "we pretend the first and last were the only fixes and ask whether the known position is inside the prism. "
    "We also move that position 50 to 150 km at random to see how often a wrong position would also be inside.",
]
CAVEATS = [
    "Result in one line: the real count (108 of 1,338 gaps) is 1.07 times the shifted-prism count (interval 0.91 to 1.25), so this data does not show that radar inside a prism beats chance. In the two Brazilian cells, which hold most of the hits, real and shifted counts are the same (33 vs 33.4 and 34 vs 35.4): those hits are traffic and structures that are there every week.",
    "Per-cell look (exploratory, not a tested claim): the three cells outside Brazil with hits show a small excess (14 vs 9.8, 18 vs 13.5, 8 vs 5.4 shifted); the sample is too small to tell it from chance.",
    "Test 2 does not show the prism is specific: it contains the true position 100% of the time, but also contains a position moved 50-150 km at random 98.4% of the time. At a median gap of 40 hours the prism covers hundreds of kilometres, so 'inside the prism' alone carries little information; the evidence has to come from radar cells that are rare in that water, which Test 1 does not find.",
    "A few prisms made GEOS reject the lens union: 27 retries with a different time step, 9 (gap, shift) pairs counted as no hit in Test 1, 5 cases skipped in Test 2. core/prism.py was out of scope; fix it there.",
    "Test 1 uses only the 1,338 gaps in the 15 busiest cells, the ones the region scan fetched radar for; the "
    "rest of the 5,624 have no radar loaded. Those cells are dominated by Brazilian offshore-oil traffic, so the "
    "result says little about open-ocean transits.",
    "Radar is not uniform in time (Sentinel-1 revisit patterns), so shifting by a few weeks can change coverage "
    "for reasons unrelated to ships; the shuffle averages over many shifts but cannot remove this.",
    "Unmatched radar cells include fixed structures (platforms), other ships GFW could not match, and waves; "
    "the filtered variant removes cells seen on 3 or more days but cannot remove the rest.",
    "No spatial shuffle was run (see Test 1).",
    "Test 2 is a physics check, not a radar recall: its 'truth' is another GFW event position, which is itself "
    "a point inside a longer event, and vessels that stop at events are not a random sample of dark ships.",
]
WOULD_CHANGE = [
    "A real count whose bootstrap interval sits above 1 in the filtered variant on a larger set of gaps would "
    "move the claim from 'no evidence' to 'evidence'.",
    "Vessel-level attribution of matched radar detections would allow a true recall number.",
    "A land mask and radar fetched around shifted prisms would allow the spatial shuffle.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--seed", type=int, default=27)
    ap.add_argument("--env", default="/Users/kassemkanjo/Desktop/Hack Western/.env")
    ap.add_argument("--recall-target", type=int, default=150)
    ap.add_argument("--recall-max-requests", type=int, default=120)
    ap.add_argument("--max-requests", type=int, default=900, help="hard stop for live requests")
    args = ap.parse_args()
    load_dotenv(args.env)
    cli = sr._client()

    raw = sr.fetch_gaps(cli, "2026-08-15", "2026-11-15")
    gaps_all = []
    for e in raw:
        g = sr.to_gap(e)
        g["vessel_id"] = e["vessel"].get("id")
        gaps_all.append(g)
    gaps_all = [g for g in gaps_all if g["type"] != "gear" and 6 <= g["hours"] <= 96]
    print(f"kept gaps {len(gaps_all)}; live requests {sr.REQUESTS}")

    null = run_null(cli, gaps_all, args.k, args.seed)
    print(f"live requests after test 1: {sr.REQUESTS}")
    assert sr.REQUESTS < args.max_requests, "request budget exceeded"
    recall = run_recall(cli, gaps_all, args.recall_target, args.recall_max_requests, args.seed)
    print(f"live requests after test 2: {sr.REQUESTS}")
    assert sr.REQUESTS < args.max_requests, "request budget exceeded"

    results = {
        "generated": datetime.now(UTC).date().isoformat(),
        "requests_live": len(list(sr.CACHE.glob("*.json")))
        - 472,  # files beyond the 472 the region scan cached
        "null_test": null,
        "recall_test": recall,
        "method": METHOD,
        "caveats": CAVEATS,
        "would_change": WOULD_CHANGE,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=1, default=float))
    OUT_MD.write_text(render_report(results))
    print(f"wrote {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
