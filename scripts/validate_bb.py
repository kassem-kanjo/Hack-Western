# ruff: noqa: E501
"""Experiment 2 (#27): is a Brownian-bridge score specific where the binary prism is not?

    ~/.local/bin/uv run python scripts/validate_bb.py [--k 50]

The plan is pre-registered in docs/validation.md ("Experiment 2"). Reuses the cached GFW responses and the
gap/SAR/recall-case construction from scripts/validate.py; writes data/validation/bridge_results.json and
appends the results to docs/validation.md.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import scan_regions as sr  # noqa: E402
import validate as v  # noqa: E402  (importing only defines functions; main() is guarded)

from darkfleet.core.bridge import (  # noqa: E402
    bridge_logpdf,
    bridge_mean,
    day_scores,
    fit_sigma,
    paired_auc,
)
from darkfleet.core.geo import KNOT_KM_H, Fix, distance_km, distance_km_array  # noqa: E402
from darkfleet.core.speeds import max_speed_knots  # noqa: E402
from darkfleet.core.validation import (  # noqa: E402
    allowed_shifts,
    draw_shifts,
    null_summary,
    offset_point,
    paired_ratio_ci,
    random_offsets,
)

OUT_JSON = ROOT / "data" / "validation" / "bridge_results.json"
OUT_MD = ROOT / "docs" / "validation.md"
MAX_GAP_H = 48.0
MARK = "\n### Results (Experiment 2)\n"
_G: dict = {}


def _init(gaps, cells, sigma):
    _G.update(gaps=gaps, cells=cells, sigma=sigma)


def _scores_for_gap(i: int):
    g = _G["gaps"][i]
    cell = _G["cells"][g["cell_key"]]
    slat, slon, sday = cell["lat"], cell["lon"], cell["day"]
    vkn = max_speed_knots(g["type"])
    reach = vkn * KNOT_KM_H * g["hours"]
    out = []
    for d in [0, *g["allowed"].tolist()]:
        idx = np.where(
            (sday >= g["start"].date().toordinal() + d) & (sday <= g["end"].date().toordinal() + d)
        )[0]
        if len(idx):
            da = distance_km_array(g["off"][0], g["off"][1], slat[idx], slon[idx])
            db = distance_km_array(slat[idx], slon[idx], g["on"][0], g["on"][1])
            idx = idx[da + db <= reach + 1e-3]
        best = float("nan")
        if len(idx):
            dt = timedelta(days=d)
            a = Fix(g["off"][0], g["off"][1], g["start"] + dt)
            b = Fix(g["on"][0], g["on"][1], g["end"] + dt)
            sc = day_scores(a, b, vkn, _G["sigma"], slat[idx], slon[idx], sday[idx])
            fin = sc[np.isfinite(sc)]
            if fin.size:
                best = float(fin.max())
        out.append((d, best))
    return i, out


def recall_cases(cli, gaps_all, seed, target=150, max_requests=120):
    vids = sorted({g["vessel_id"] for g in gaps_all if g.get("vessel_id")})
    random.Random(seed).shuffle(vids)
    events, cases, start = [], [], sr.REQUESTS
    for i in range(0, len(vids), 10):
        if len(cases) >= target or sr.REQUESTS - start >= max_requests:
            break
        events += v.fetch_events(cli, vids[i : i + 10], "2026-08-15", "2026-11-15")
        cases = v.build_cases(events)
    return cases


def fit_and_auc(cases, seed):
    rng = np.random.default_rng(seed)
    d_list, w_list, rows = [], [], []
    for cs in cases:
        a = Fix(*cs["a"]["pos"], cs["a"]["end"])
        b = Fix(*cs["b"]["pos"], cs["b"]["start"])
        lat, lon = cs["c"]["pos"]
        t = cs["c"]["start"]
        T = (b.t - a.t).total_seconds() / 3600
        tau = (t - a.t).total_seconds() / 3600 / T
        mlat, mlon = bridge_mean(a, b, t)
        d_list.append(distance_km(mlat, mlon, lat, lon))
        w_list.append(tau * (1 - tau) * T)
        rows.append((a, b, lat, lon, t))
    sigma = fit_sigma(np.array(d_list), np.array(w_list))
    truth, disp = [], []
    for a, b, lat, lon, t in rows:
        truth.append(bridge_logpdf(a, b, sigma, lat, lon, t))
        dist, brg = random_offsets(rng, 20)
        disp.append(
            [
                bridge_logpdf(a, b, sigma, *offset_point(lat, lon, float(x), float(y)), t)
                for x, y in zip(dist, brg, strict=True)
            ]
        )
    auc, lo, hi = paired_auc(np.array(truth), np.array(disp), np.random.default_rng(seed + 2))
    return sigma, {"auc": auc, "lo": lo, "hi": hi, "n_cases": len(rows), "n_pairs": len(rows) * 20}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--seed", type=int, default=27)
    ap.add_argument("--env", default="/Users/kassemkanjo/Desktop/Hack Western/.env")
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

    # step 1: sigma from recall cases only
    cases = recall_cases(cli, gaps_all, args.seed)
    sigma, auc = fit_and_auc(cases, args.seed)
    print(f"sigma {sigma:.3f} km/sqrt(h) from {len(cases)} cases; AUC {auc}")

    # step 2: scores per (gap, shift)
    bins = defaultdict(list)
    for g in gaps_all:
        la, lo = sr.midpoint(g)
        bins[(int(np.floor(la / 5) * 5), int(np.floor(lo / 5) * 5))].append(g)
    top = sorted(bins.items(), key=lambda kv: -len(kv[1]))[:15]
    cells, gaps = {}, []
    for (la0, lo0), gl in top:
        rows = sr.fetch_sar(cli, (lo0 - 1.0, la0 - 1.0, lo0 + 6.0, la0 + 6.0), v.S_MIN, v.S_MAX)
        cells[(la0, lo0)] = {
            "lat": np.array([r["lat"] for r in rows], float),
            "lon": np.array([r["lon"] for r in rows], float),
            "day": np.array([date.fromisoformat(r["date"][:10]).toordinal() for r in rows], int),
        }
        for g in gl:
            if g["hours"] > MAX_GAP_H:
                continue
            dmin = (v.S_MIN - g["start"].date()).days
            dmax = (v.S_MAX - g["end"].date()).days
            gaps.append(
                dict(g, cell_key=(la0, lo0), allowed=allowed_shifts(v.SHIFT_LO, v.SHIFT_HI, dmin, dmax))
            )
    n = len(gaps)
    print(f"gaps <= {MAX_GAP_H:g} h in the 15 cells: {n}")
    with mp.get_context("spawn").Pool(
        min(8, os.cpu_count() or 2), initializer=_init, initargs=(gaps, cells, sigma)
    ) as pool:
        res = dict(pool.imap_unordered(_scores_for_gap, range(n), chunksize=4))
    off = v.SHIFT_HI
    table = np.full((n, 2 * off + 1), np.nan)
    for i, rows in res.items():
        for d, s in rows:
            table[i, d + off] = s

    # step 3: null
    rng = np.random.default_rng(args.seed)
    has = np.array([len(g["allowed"]) > 0 for g in gaps])
    table = table[has]
    drawn = draw_shifts(rng, [g["allowed"] for g in gaps if len(g["allowed"])], args.k)
    real = table[:, off]
    shuf = np.take_along_axis(table, drawn + off, axis=1)
    k = args.k
    a_summary = null_summary(int(np.isfinite(real).sum()), np.isfinite(shuf).sum(axis=0))
    real_mean = float(np.nanmean(real))
    shuf_means = np.nanmean(shuf, axis=0)
    p_mean = (1 + int((shuf_means >= real_mean).sum())) / (1 + k)
    thr = float(np.percentile(shuf[np.isfinite(shuf)], 95))
    ex_real = np.nan_to_num(real, nan=-np.inf) > thr
    ex_shuf = np.nan_to_num(shuf, nan=-np.inf) > thr
    c_summary = null_summary(int(ex_real.sum()), ex_shuf.sum(axis=0))
    ratio, lo, hi = paired_ratio_ci(
        ex_real.astype(float), ex_shuf.astype(float), np.random.default_rng(args.seed + 1)
    )
    sig_c = (lo > 1.0) or c_summary["p_value"] < 0.05
    sig_b = p_mean < 0.05
    sig_auc = auc["lo"] > 0.5
    verdict = "signal" if ((sig_b or sig_c) and sig_auc) else "no signal"
    out = {
        "sigma_km_per_sqrt_h": sigma,
        "n_recall_cases": len(cases),
        "n_gaps_le_48h": int(len(table)),
        "a_scorable_gaps": a_summary,
        "b_mean_score": {
            "real": real_mean,
            "shuffled_mean": float(shuf_means.mean()),
            "null_lo": float(np.percentile(shuf_means, 2.5)),
            "null_hi": float(np.percentile(shuf_means, 97.5)),
            "p_value": p_mean,
        },
        "c_exceed": {
            "threshold": thr,
            **c_summary,
            "ratio": None if np.isnan(ratio) else ratio,
            "ratio_lo": None if np.isnan(lo) else lo,
            "ratio_hi": None if np.isnan(hi) else hi,
            "expected_fraction": 0.05,
            "scorable_real": int(np.isfinite(real).sum()),
        },
        "d_auc": auc,
        "verdict": verdict,
    }
    OUT_JSON.write_text(json.dumps(out, indent=1, default=float))
    md = render(out)
    text = OUT_MD.read_text()
    text = text.split(MARK)[0].rstrip("\n") + "\n" + MARK + md
    OUT_MD.write_text(text)
    print(json.dumps(out, indent=1, default=float))


def render(o: dict) -> str:
    a, b, c, d = o["a_scorable_gaps"], o["b_mean_score"], o["c_exceed"], o["d_auc"]

    def f(x, nd=2):
        return "n/a" if x is None else f"{x:,.{nd}f}"

    lines = [
        f"Fitted sigma = {o['sigma_km_per_sqrt_h']:.2f} km per square-root hour from {o['n_recall_cases']} recall cases. "
        f"Gaps of 48 hours or less in the 15 radar cells: {o['n_gaps_le_48h']:,}. K = {a['k']} shifted-prism shuffles.",
        "",
        "| Measure | Real | Shifted (mean, 95% range) | Ratio or p |",
        "| --- | --- | --- | --- |",
        f"| Gaps with a scorable radar cell | {a['real']} | {f(a['shuffled_mean'], 1)} ({f(a['null_lo'], 1)} to {f(a['null_hi'], 1)}) | ratio {f(a['ratio'])}, p {a['p_value']:.3f} |",
        f"| Mean best log-density over scorable gaps | {f(b['real'])} | {f(b['shuffled_mean'])} ({f(b['null_lo'])} to {f(b['null_hi'])}) | p {b['p_value']:.3f} |",
        f"| Gaps above the null 95th percentile ({f(c['threshold'])}) | {c['real']} | {f(c['shuffled_mean'], 1)} ({f(c['null_lo'], 1)} to {f(c['null_hi'], 1)}); 5% of scorable gaps = {0.05 * c['scorable_real']:.1f} | ratio {f(c['ratio'])} ({f(c['ratio_lo'])} to {f(c['ratio_hi'])}), p {c['p_value']:.3f} |",
        "",
        f"Recall-side check: the true position's bridge density beats a position displaced 50 to 150 km in "
        f"{d['auc'] * 100:.1f}% of {d['n_pairs']:,} pairs (AUC {d['auc']:.3f}, 95% interval {d['lo']:.3f} to {d['hi']:.3f}, "
        f"{d['n_cases']} cases).",
        "",
        f"Verdict: {o['verdict']}. (Rule from the pre-registration: signal needs the count ratio interval above 1 or "
        "p < 0.05 on the mean score or the count, and the AUC interval above 0.5.)",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
