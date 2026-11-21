# ruff: noqa: E501
"""Experiment 3 (#27): held-out test outside Brazil. Pre-registered in docs/validation.md.

    ~/.local/bin/uv run python scripts/validate_holdout.py

Reuses the Test 1 machinery (scripts/validate.py) and the Experiment 2 scorer (scripts/validate_bb.py);
only the data window and the cell list differ. Writes data/validation/holdout_results.json and appends the
results to docs/validation.md.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import scan_regions as sr  # noqa: E402
import validate as v  # noqa: E402
import validate_bb as bb  # noqa: E402

from darkfleet.core.geo import distance_km  # noqa: E402
from darkfleet.core.validation import (  # noqa: E402
    allowed_shifts,
    draw_shifts,
    interpret_ratio,
    null_summary,
    paired_ratio_ci,
)

GAP_START, GAP_END = "2026-05-01", "2026-08-14"
S_MIN, S_MAX = date(2026, 4, 10), date(2026, 9, 4)
CELLS = [
    (40, 170),
    (0, 165),
    (-20, -80),
    (5, -50),
    (10, -170),
    (40, 155),
    (40, 175),
    (-35, 95),
    (-35, 100),
    (40, 150),
    (-20, 170),
    (10, -50),
    (-20, -130),
]
OUT_JSON = ROOT / "data" / "validation" / "holdout_results.json"
OUT_MD = ROOT / "docs" / "validation.md"
MARK = "\n### Results (Experiment 3)\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--seed", type=int, default=27)
    ap.add_argument("--env", default="/Users/kassemkanjo/Desktop/Hack Western/.env")
    ap.add_argument("--max-requests", type=int, default=1500)
    args = ap.parse_args()
    load_dotenv(args.env)
    cli = sr._client()
    n_cache0 = len(list(sr.CACHE.glob("*.json")))

    raw = sr.fetch_gaps(cli, GAP_START, GAP_END)
    gaps_all = [sr.to_gap(e) for e in raw]
    n_events = len(gaps_all)
    gaps_all = [g for g in gaps_all if g["type"] != "gear" and 6 <= g["hours"] <= 96]
    print(
        f"events {n_events}, kept {len(gaps_all)}; cache files added so far {len(list(sr.CACHE.glob('*.json'))) - n_cache0}"
    )

    bins = defaultdict(list)
    for g in gaps_all:
        la, lo = sr.midpoint(g)
        bins[(int(np.floor(la / 5) * 5), int(np.floor(lo / 5) * 5))].append(g)

    cells, gaps = {}, []
    per_cell = []
    for key in CELLS:
        la0, lo0 = key
        rows = sr.fetch_sar(cli, (lo0 - 1.0, la0 - 1.0, lo0 + 6.0, la0 + 6.0), S_MIN, S_MAX)
        lat = np.array([r["lat"] for r in rows], float)
        lon = np.array([r["lon"] for r in rows], float)
        day = np.array([date.fromisoformat(r["date"][:10]).toordinal() for r in rows], int)
        days_at = defaultdict(set)
        for a, b, dd in zip(lat, lon, day, strict=True):
            days_at[(float(a), float(b))].add(int(dd))
        fixed = {p for p, ds in days_at.items() if len(ds) >= v.FIXED_DAYS}
        cells[key] = {"lat": lat, "lon": lon, "day": day, "fixed": fixed}
        gl = bins.get(key, [])
        per_cell.append(
            {"cell": f"{la0}..{la0 + 5}N, {lo0}..{lo0 + 5}E", "gaps": len(gl), "sar_cells": len(rows)}
        )
        for g in gl:
            g = dict(g, cell_key=key)
            g["off_on_km"] = distance_km(g["off"][0], g["off"][1], g["on"][0], g["on"][1])
            g["allowed"] = allowed_shifts(
                v.SHIFT_LO,
                v.SHIFT_HI,
                (S_MIN - g["start"].date()).days,
                (S_MAX - g["end"].date()).days,
            )
            gaps.append(g)
        print(per_cell[-1])
    n = len(gaps)
    added = len(list(sr.CACHE.glob("*.json"))) - n_cache0
    assert added <= args.max_requests, f"request budget exceeded: {added}"

    # primary: Test 1 flags
    with mp.get_context("spawn").Pool(
        min(8, os.cpu_count() or 2), initializer=v._init_worker, initargs=(gaps, cells)
    ) as pool:
        raw_res = list(pool.imap_unordered(v._flags_for_gap, range(n), chunksize=4))
    off = v.SHIFT_HI
    tab = np.full((n, 2 * off + 1), -1, int)
    for i, rows, _ in raw_res:
        for d, _a, nf in rows:
            tab[i, d + off] = nf
    rng = np.random.default_rng(args.seed)
    has = np.array([len(g["allowed"]) > 0 for g in gaps])
    moving = np.array([g["off_on_km"] >= v.STATIONARY_KM for g in gaps])
    sel = has & moving
    drawn = draw_shifts(rng, [g["allowed"] for g in gaps], args.k)
    t = tab[sel]
    real = t[:, off]
    shuf = np.take_along_axis(t, drawn[sel] + off, axis=1)
    assert (real >= 0).all() and (shuf >= 0).all()
    summ = null_summary(int(real.sum()), shuf.sum(axis=0))
    ratio, lo, hi = paired_ratio_ci(real, shuf, np.random.default_rng(args.seed + 1))
    primary = {
        "n_gaps_kept_in_cells": n,
        "n_gaps_tested": int(sel.sum()),
        **summ,
        "ratio": None if np.isnan(ratio) else ratio,
        "ratio_lo": None if np.isnan(lo) else lo,
        "ratio_hi": None if np.isnan(hi) else hi,
        "interpretation": interpret_ratio(ratio, lo, hi),
    }
    verdict_primary = (
        "signal"
        if (not np.isnan(lo) and lo > 1.0)
        else ("inconclusive (fewer than 20 real hits)" if int(real.sum()) < 20 else "no signal")
    )

    # secondary: Experiment 2 procedure
    sigma = json.loads((ROOT / "data" / "validation" / "bridge_results.json").read_text())[
        "sigma_km_per_sqrt_h"
    ]
    idx48 = [i for i, g in enumerate(gaps) if g["hours"] <= bb.MAX_GAP_H]
    sub = [gaps[i] for i in idx48]
    with mp.get_context("spawn").Pool(
        min(8, os.cpu_count() or 2), initializer=bb._init, initargs=(sub, cells, sigma)
    ) as pool:
        res = dict(pool.imap_unordered(bb._scores_for_gap, range(len(sub)), chunksize=4))
    tb = np.full((len(sub), 2 * off + 1), np.nan)
    for i, rows in res.items():
        for d, s in rows:
            tb[i, d + off] = s
    has_b = np.array([len(g["allowed"]) > 0 for g in sub])
    tb = tb[has_b]
    drawn_b = draw_shifts(
        np.random.default_rng(args.seed), [g["allowed"] for g in sub if len(g["allowed"])], args.k
    )
    rb = tb[:, off]
    sb = np.take_along_axis(tb, drawn_b + off, axis=1)
    sec: dict = {"n_gaps_le_48h": int(len(tb)), "scorable_real": int(np.isfinite(rb).sum())}
    if np.isfinite(sb).any() and np.isfinite(rb).any():
        a_sum = null_summary(int(np.isfinite(rb).sum()), np.isfinite(sb).sum(axis=0))
        real_mean = float(np.nanmean(rb))
        sm = np.nanmean(sb, axis=0)
        p_mean = (1 + int((sm >= real_mean).sum())) / (1 + args.k)
        thr = float(np.percentile(sb[np.isfinite(sb)], 95))
        er = np.nan_to_num(rb, nan=-np.inf) > thr
        es = np.nan_to_num(sb, nan=-np.inf) > thr
        c_sum = null_summary(int(er.sum()), es.sum(axis=0))
        r2, l2, h2 = paired_ratio_ci(er.astype(float), es.astype(float), np.random.default_rng(args.seed + 1))
        sec.update(
            scorable_gaps=a_sum,
            mean_score={"real": real_mean, "shuffled_mean": float(np.nanmean(sm)), "p_value": p_mean},
            exceed={
                "threshold": thr,
                **c_sum,
                "ratio": None if np.isnan(r2) else r2,
                "ratio_lo": None if np.isnan(l2) else l2,
                "ratio_hi": None if np.isnan(h2) else h2,
            },
        )
        sig2 = (not np.isnan(l2) and l2 > 1.0) or c_sum["p_value"] < 0.05 or p_mean < 0.05
        sec["verdict"] = "signal" if sig2 else "no signal"
    else:
        sec["verdict"] = "inconclusive (no scorable cells)"

    out = {
        "events_total": n_events,
        "cells": per_cell,
        "requests_live": added,
        "primary": primary,
        "primary_verdict": verdict_primary,
        "secondary_bridge": sec,
    }
    OUT_JSON.write_text(json.dumps(out, indent=1, default=float))
    OUT_MD.write_text(OUT_MD.read_text().split(MARK)[0].rstrip("\n") + "\n" + MARK + render(out))
    print(
        json.dumps(
            {k: out[k] for k in ("requests_live", "primary", "primary_verdict", "secondary_bridge")},
            indent=1,
            default=float,
        )
    )


def render(o: dict) -> str:
    p, s = o["primary"], o["secondary_bridge"]

    def f(x, nd=2):
        return "n/a" if x is None else f"{x:,.{nd}f}"

    cells_used = sum(1 for c in o["cells"] if c["sar_cells"] > 0)
    lines = [
        f"Live GFW requests: {o['requests_live']}. {o['events_total']:,} gap events in 2026-05-01 to 2026-08-14; "
        f"{p['n_gaps_kept_in_cells']:,} non-gear gaps of 6 to 96 h have their midpoint in the 13 cells, of which {p['n_gaps_tested']:,} are moving gaps. "
        f"{cells_used} of the 13 cells have any radar in the window.",
        "",
        "| Cell | Gaps | Radar cells fetched |",
        "| --- | --- | --- |",
        *[f"| {c['cell']} | {c['gaps']} | {c['sar_cells']} |" for c in o["cells"]],
        "",
        "Primary (day-lens, moving gaps, recurring radar cells dropped, K = 50 shifts of 7 to 21 days):",
        "",
        "| Real | Shifted mean (95% range) | Ratio (gap-bootstrap 95% interval) | p |",
        "| --- | --- | --- | --- |",
        f"| {p['real']} | {f(p['shuffled_mean'], 1)} ({f(p['null_lo'], 1)} to {f(p['null_hi'], 1)}) | {f(p['ratio'])} ({f(p['ratio_lo'])} to {f(p['ratio_hi'])}) | {p['p_value']:.3f} |",
        "",
        f"Primary verdict: {o['primary_verdict']}.",
        "",
        f"Secondary, bridge score (Experiment 2 procedure, {s['n_gaps_le_48h']:,} gaps of 48 h or less, {s['scorable_real']} with a scorable cell):",
    ]
    if "exceed" in s:
        e, m, a = s["exceed"], s["mean_score"], s["scorable_gaps"]
        lines += [
            "",
            f"- Scorable gaps: {a['real']} real vs {f(a['shuffled_mean'], 1)} shifted.",
            f"- Mean best log-density: {f(m['real'])} real vs {f(m['shuffled_mean'])} shifted, p {m['p_value']:.3f}.",
            f"- Gaps above the null 95th percentile ({f(e['threshold'])}): {e['real']} real vs {f(e['shuffled_mean'], 1)} expected; ratio {f(e['ratio'])} ({f(e['ratio_lo'])} to {f(e['ratio_hi'])}), p {e['p_value']:.3f}.",
        ]
    lines += ["", f"Secondary verdict: {s['verdict']}."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
