"""Validate the Baltic engine on our own AIS tracks (issue #74, pure metrics in core).

    ~/.local/bin/uv run python scripts/validate_tracks.py [--jsonl-dir DIR] [--seed 7]

Hides random 1-12 hour underway stretches of the tracks the recorder already stored and
asks four questions of the engine: does the space-time prism contain the hidden path, does
the Brownian bridge rank that path above positions displaced 20-80 km, does the silence
detector find the gaps we cut, and do the jump/clone rules stay quiet on clean tracks.
The hidden pings are ground truth because the recorder received them, so no external
dataset is needed. Writes data/validation/tracks_results.json and docs/validation-tracks.md.

An empty store is not an error: the script prints "no data", records that in both outputs
and exits 0, so a fresh checkout can run it before the recorder has anything.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from darkfleet.core.bridge import bridge_mean, fit_sigma  # noqa: E402
from darkfleet.core.geo import distance_km  # noqa: E402
from darkfleet.core.speeds import max_speed_knots  # noqa: E402
from darkfleet.core.track_validation import (  # noqa: E402
    MaskCase,
    bridge_auc,
    containment,
    detector_recall,
    false_positive_rate,
    inject_gaps,
    mask_segments,
)
from darkfleet.core.tracks import CoverageMinute, Event, Ping, ship_class  # noqa: E402
from darkfleet.store.factory import make_repo  # noqa: E402

OUT_JSON = ROOT / "data" / "validation" / "tracks_results.json"
OUT_MD = ROOT / "docs" / "validation-tracks.md"
DEFAULT_SIGMA = 5.0  # km per sqrt(hour): the value scripts/validate_bb.py fits on GFW cases
MAX_SCORED_TRUTHS = 40  # hidden fixes per window that containment checks (see METHOD)
MAX_FIT_CASES = 40  # windows used to fit sigma; enough to converge, capped for speed


@dataclass(frozen=True, slots=True)
class Track:
    """One vessel's time-ordered pings plus the speed class its static data implies."""

    vessel_class: str
    pings: tuple[Ping, ...]


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO timestamp from the store; naive values are taken as UTC."""
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _pings_from_repo(repo: Any) -> list[Ping]:
    """Read positions from the local store, tolerating a build without the table.

    The repository protocol has no positions reader yet (issue #72 adds one), so this
    goes through the SQLite connection when the backend exposes it. Any other backend,
    or a database created before the AIS lane landed, reads as "no data" rather than an
    error, which is what an empty store should look like.
    """
    conn = getattr(repo, "_conn", None)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT mmsi, ts, lat, lon, sog FROM positions ORDER BY ts").fetchall()
    except sqlite3.Error:
        return []
    pings: list[Ping] = []
    for mmsi, ts, lat, lon, sog in rows:
        t = _parse_ts(str(ts))
        if t is None:
            continue
        try:
            mmsi_int = int(mmsi)
        except (TypeError, ValueError):
            continue
        pings.append(Ping(mmsi=mmsi_int, t=t, lat=float(lat), lon=float(lon), sog=sog))
    return pings


def _vessel_classes(repo: Any) -> dict[int, str]:
    """Map MMSI to a speed class when the static table exists; empty otherwise."""
    conn = getattr(repo, "_conn", None)
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT mmsi, ship_type FROM vessels_static").fetchall()
    except sqlite3.Error:
        return {}
    out: dict[int, str] = {}
    for mmsi, ship_type in rows:
        try:
            out[int(mmsi)] = ship_class(int(ship_type) if ship_type is not None else None)
        except (TypeError, ValueError):
            continue
    return out


def _coverage_from_repo(repo: Any) -> list[CoverageMinute]:
    """Read the receiver's per-minute message counts, when the table exists."""
    conn = getattr(repo, "_conn", None)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT minute, n_msgs FROM coverage_minutes ORDER BY minute").fetchall()
    except sqlite3.Error:
        return []
    out: list[CoverageMinute] = []
    for minute, n_msgs in rows:
        t = _parse_ts(str(minute))
        if t is None:
            continue
        out.append(CoverageMinute(minute=t, n_msgs=int(n_msgs)))
    return out


def _load_jsonl(directory: Path) -> list[Ping]:
    """Parse a directory of aisstream lines through the ingest lane, if it exists."""
    try:
        from darkfleet.ingest import ais_tracks
    except ImportError:
        print("skipping --jsonl-dir: darkfleet.ingest.ais_tracks (issue #72) is not in this build")
        return []
    paths = sorted([*directory.glob("*.jsonl"), *directory.glob("*.jsonl.gz")])
    if not paths:
        print(f"skipping --jsonl-dir: no .jsonl or .jsonl.gz files in {directory}")
        return []
    pings: list[Ping] = []
    for item in ais_tracks.iter_files(paths):
        obj = item
        if isinstance(item, str):
            obj = ais_tracks.parse_line(item)
        if isinstance(obj, Ping):
            pings.append(obj)
    return pings


def _thin(pings: Sequence[Ping], minutes: float) -> list[Ping]:
    """Keep one ping per interval so a 12 h window has a tractable number of truths.

    Recorders in the Baltic see the same ship every few seconds; masking such a track
    directly would produce thousands of hidden fixes per window and hours of geodesy
    for no extra statistical power. Thinning never drops the first ping after a real
    outage, because it is more than one interval after the previous kept one.
    """
    if minutes <= 0 or len(pings) < 2:
        return list(pings)
    step = timedelta(minutes=minutes)
    out = [pings[0]]
    for p in pings[1:]:
        if p.t - out[-1].t >= step:
            out.append(p)
    return out


def _group(
    pings: Sequence[Ping],
    classes: dict[int, str],
    *,
    max_vessels: int,
    min_pings: int,
    ping_gap_min: float,
) -> list[Track]:
    """Split positions into per-MMSI tracks, thin them, and keep the longest-lived ones.

    The AIS lane's ``split_tracks`` (issue #73) does the canonical sort/dedupe/junk
    filter; until it is in the build this does the minimum needed by the harness. Tracks
    are ordered by first ping so the printed sample is stable across runs with one seed.
    """
    by_mmsi: defaultdict[int, list[Ping]] = defaultdict(list)
    for p in pings:
        by_mmsi[p.mmsi].append(p)
    tracks: list[Track] = []
    for mmsi, group in by_mmsi.items():
        ordered = sorted(group, key=lambda p: p.t)
        deduped: list[Ping] = []
        for p in ordered:
            if not deduped or p.t != deduped[-1].t:
                deduped.append(p)
        thinned = _thin(deduped, ping_gap_min)
        if len(thinned) < min_pings:
            continue
        tracks.append(Track(vessel_class=classes.get(mmsi, "default"), pings=tuple(thinned)))
    tracks.sort(key=lambda tr: tr.pings[0].t)
    return tracks[:max_vessels]


def _load(args: argparse.Namespace) -> tuple[list[Track], int, list[CoverageMinute], str]:
    """Return tracks, the raw position count, coverage, and where the data came from."""
    if args.jsonl_dir:
        pings = _load_jsonl(Path(args.jsonl_dir))
        source = f"aisstream files in {args.jsonl_dir}"
        coverage: list[CoverageMinute] = []
        tracks = _group(
            pings,
            {},
            max_vessels=args.max_vessels,
            min_pings=args.min_pings,
            ping_gap_min=args.ping_gap_min,
        )
        return tracks, len(pings), coverage, source
    repo = make_repo()
    try:
        pings = _pings_from_repo(repo)
        classes = _vessel_classes(repo)
        coverage = _coverage_from_repo(repo)
    finally:
        repo.close()
    tracks = _group(
        pings,
        classes,
        max_vessels=args.max_vessels,
        min_pings=args.min_pings,
        ping_gap_min=args.ping_gap_min,
    )
    return tracks, len(pings), coverage, "positions in the darkfleet store"


def _trim_case(case: MaskCase, max_truths: int) -> MaskCase:
    """Keep evenly spaced hidden fixes so one dense window cannot dominate runtime."""
    if max_truths <= 0 or len(case.truths) <= max_truths:
        return case
    idx = np.linspace(0, len(case.truths) - 1, max_truths).round().astype(int)
    return replace(case, truths=[case.truths[int(i)] for i in idx])


def _fit_bridge_sigma(cases: Sequence[MaskCase]) -> float:
    """Fit the bridge's km/sqrt(hour) noise scale from the hidden fixes we have.

    ``fit_sigma`` is the maximum-likelihood estimate given each fix's distance from the
    bridge mean and its ``tau * (1 - tau) * T`` weight. It needs a positive weight and a
    nonzero spread; straight synthetic tracks can give a near-zero estimate, so the
    result is clamped to at least 1 km/sqrt(h) to keep the density well defined.
    """
    d_km: list[float] = []
    w_hours: list[float] = []
    for case in cases[:MAX_FIT_CASES]:
        total = (case.b.t - case.a.t).total_seconds() / 3600.0
        if total <= 0:
            continue
        for p in _trim_case(case, 8).truths:
            tau = (p.t - case.a.t).total_seconds() / 3600.0 / total
            weight = tau * (1.0 - tau) * total
            if weight <= 0:
                continue
            mlat, mlon = bridge_mean(case.a, case.b, p.t)
            d_km.append(distance_km(mlat, mlon, p.lat, p.lon))
            w_hours.append(weight)
    try:
        sigma = fit_sigma(np.asarray(d_km), np.asarray(w_hours))
    except (ValueError, ZeroDivisionError):
        return DEFAULT_SIGMA
    if not math.isfinite(sigma) or sigma <= 0:
        return DEFAULT_SIGMA
    return max(sigma, 1.0)


def _kinematics(name: str) -> Callable[..., list[Event]] | None:
    """Look up a detector from the kinematics lane, or None when it is not in this build."""
    try:
        module = importlib.import_module("darkfleet.core.kinematics")
    except ImportError:
        return None
    fn = getattr(module, name, None)
    return fn if callable(fn) else None


def _detection(
    args: argparse.Namespace,
    tracks: Sequence[Track],
    coverage: Sequence[CoverageMinute],
    rng: np.random.Generator,
) -> dict[str, Any]:
    """Injected-gap recall for the silence detector, or why it could not be measured."""
    silences = _kinematics("silences")
    if silences is None:
        return {"status": "skipped", "reason": "core.kinematics (issue #73) is not in this build"}
    if not coverage:
        return {
            "status": "skipped",
            "reason": "the store has no coverage_minutes, so a receiver outage cannot be told from a ship's",
        }
    masked: list[tuple[Sequence[Ping], Sequence[tuple[datetime, datetime]]]] = []
    for tr in tracks[: args.max_detect_tracks]:
        kept, gaps = inject_gaps(tr.pings, rng, n=args.gaps_per_track)
        masked.append((kept, gaps))

    def detector(pings: Sequence[Ping], cov: Sequence[CoverageMinute]) -> list[Event]:
        return silences(pings, cov, min_gap_min=args.min_gap_min, min_sog=2.0)

    return {
        "status": "ok",
        "min_gap_min": args.min_gap_min,
        "gaps_per_track": args.gaps_per_track,
        **detector_recall(masked, coverage, detector),
    }


def _false_positives(args: argparse.Namespace, tracks: Sequence[Track]) -> dict[str, Any]:
    """Jump/clone events per vessel-day on tracks that had nothing removed."""
    jumps = _kinematics("jumps")
    clones = _kinematics("clones")
    if jumps is None or clones is None:
        return {"status": "skipped", "reason": "core.kinematics (issue #73) is not in this build"}
    cap = max_speed_knots("default")

    def detector(pings: Sequence[Ping]) -> list[Event]:
        return [*jumps(pings, cap), *clones(pings)]

    clean = [tr.pings for tr in tracks[: args.max_fp_tracks]]
    return {"status": "ok", "cap_knots": cap, **false_positive_rate(clean, detector)}


def _run(
    args: argparse.Namespace,
    tracks: Sequence[Track],
    n_pings: int,
    coverage: Sequence[CoverageMinute],
    source: str,
) -> dict[str, Any]:
    """Compute every metric from one seeded run."""
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    base = {
        "generated_at": generated,
        "seed": args.seed,
        "source": source,
        "pings": n_pings,
        "tracks": len(tracks),
    }
    if not tracks:
        print("no data")
        return {
            **base,
            "status": "no data",
            "reason": "the store has no AIS positions (and no usable --jsonl-dir was given)",
        }

    rng = np.random.default_rng(args.seed)
    cases: list[MaskCase] = []
    for tr in tracks:
        cases.extend(mask_segments(tr, rng))
    if len(cases) > args.max_cases:
        picked = np.sort(rng.choice(len(cases), size=args.max_cases, replace=False))
        cases = [cases[int(i)] for i in picked]
    cases = [_trim_case(c, MAX_SCORED_TRUTHS) for c in cases]

    caps = {"default": max_speed_knots("default")}
    for tr in tracks:
        caps.setdefault(tr.vessel_class, max_speed_knots(tr.vessel_class))

    cont = containment(cases, caps)
    sigma = _fit_bridge_sigma(cases)
    bridge = {"sigma_km_per_sqrt_h": sigma, **bridge_auc(cases, sigma, rng, n_decoys=args.decoys)}
    detection = _detection(args, tracks, coverage, rng)
    fp = _false_positives(args, tracks)

    print(
        f"tracks {len(tracks)} pings {n_pings} masked windows {cont['n']}; "
        f"containment {_pct(cont['rate'])}, AUC {_num(bridge['auc'])}"
    )
    return {
        **base,
        "status": "ok",
        "config": {
            "vessels_used": len(tracks),
            "max_vessels": args.max_vessels,
            "max_cases": args.max_cases,
            "decoys": args.decoys,
            "ping_gap_min": args.ping_gap_min,
            "max_scored_truths_per_case": MAX_SCORED_TRUTHS,
            "mask_hours": [1, 12],
            "mask_min_sog": 2.0,
        },
        "caps_knots": caps,
        "containment": cont,
        "bridge": bridge,
        "detection": detection,
        "false_positive": fp,
    }


def _jsonable(value: Any) -> Any:
    """Replace NaN/inf with None so the output is valid JSON for every reader."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{number:,.{digits}f}"


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{100.0 * number:.1f}%"


def _count(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "n/a"


def _ci(bounds: Any, formatter: Callable[[Any], str] = _num) -> str:
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return "n/a"
    return f"{formatter(bounds[0])} to {formatter(bounds[1])}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


METHOD = [
    "We use the AIS positions our own recorder already received; no external dataset is "
    "consulted and nothing is fetched.",
    "For each track we hide random stretches between 1 and 12 hours long in which the ship "
    "was underway (speed at least 2 knots). The last ping before a stretch and the first "
    "one after it stay visible, and every ping inside is the ground truth.",
    "Containment asks whether the space-time prism built from those two visible fixes covers "
    "every hidden fix: once anywhere in the prism, and once inside the lens at the hidden "
    "fix's own time. A window counts only if all its hidden fixes are covered.",
    "Bridge AUC scores each hidden path with the Brownian bridge and compares it with the same "
    "paths displaced 20 to 80 km at random, 20 decoys per window. AUC 1.0 means the true "
    "path always ranks first; 0.5 means the bridge cannot tell them apart.",
    "Gap recall cuts 1 to 6 hour stretches out of clean tracks, then runs the silence detector "
    "on what is left. A cut counts as found when a reported silence covers at least half of it.",
    "False positives run the jump and clone rules on the same clean tracks and count events per "
    "vessel-day, where vessel-days are the summed observed time span of every track.",
]


def _render_no_data(r: dict[str, Any]) -> str:
    return (
        "\n".join(
            [
                "# Validating the Baltic engine on our own AIS tracks",
                "",
                f"Generated: {r['generated_at']}",
                f"Seed: {r['seed']}",
                "",
                "## Result",
                "",
                "No data. The darkfleet store has no AIS positions yet, so there is nothing to "
                "hide and nothing to recover. This is expected on a fresh checkout and is not a "
                "test failure.",
                "",
                f"Source checked: {r['source']}.",
                "",
                "## What to run once the recorder has data",
                "",
                "    ~/.local/bin/uv run python scripts/validate_tracks.py",
                "",
                "or, before the ingest lane exists, point it at raw aisstream files:",
                "",
                "    ~/.local/bin/uv run python scripts/validate_tracks.py --jsonl-dir data/cache/ais",
                "",
                "Data: our own AIS recorder. No Global Fishing Watch data is used in this report.",
            ]
        )
        + "\n"
    )


def _render(r: dict[str, Any]) -> str:
    cont = r["containment"]
    bridge = r["bridge"]
    detection = r["detection"]
    fp = r["false_positive"]

    lines = [
        "# Validating the Baltic engine on our own AIS tracks",
        "",
        f"Generated: {r['generated_at']}",
        f"Seed: {r['seed']} (fixed, so every number below is reproducible)",
        f"Source: {r['source']}",
        "",
        "## What we measured",
        "",
        *[f"- {item}" for item in METHOD],
        "",
        "## Numbers",
        "",
        *_table(
            ["Measure", "Value"],
            [
                ["Positions read", _count(r["pings"])],
                ["Vessels with enough pings", _count(r["tracks"])],
                ["Masked windows scored", _count(cont["n"])],
                [
                    "Hidden windows inside the whole footprint",
                    f"{_count(cont['inside_footprint'])} / {_count(cont['n'])}",
                ],
                [
                    "Hidden windows inside the lens at each hidden time",
                    f"{_count(cont['inside_lens_at_truth_time'])} / {_count(cont['n'])} "
                    f"({_pct(cont['rate'])}, 95% interval {_ci(cont['wilson_ci'], _pct)})",
                ],
                [
                    f"Bridge AUC against {bridge['n_decoys']} decoys "
                    f"{_num(bridge['min_km'], 0)}-{_num(bridge['max_km'], 0)} km away",
                    f"{_num(bridge['auc'])} (95% interval {_ci(bridge['ci'])}) over "
                    f"{_count(bridge['n_cases'])} windows",
                ],
                ["Bridge sigma used", f"{_num(bridge['sigma_km_per_sqrt_h'], 2)} km/sqrt(h)"],
            ],
        ),
        "",
        "### Silence-detector recall on injected gaps",
        "",
    ]
    if detection.get("status") == "ok":
        lines.extend(
            _table(
                ["Measure", "Value"],
                [
                    ["Tracks with injections", _count(detection["tracks"])],
                    ["Gaps injected", _count(detection["injected"])],
                    ["Gaps found", _count(detection["found"])],
                    ["Recall", f"{_pct(detection['recall'])}"],
                ],
            )
        )
    else:
        lines.append(f"Skipped: {detection.get('reason', 'unknown')}.")

    lines.extend(["", "### Jump/clone false positives on clean tracks", ""])
    if fp.get("status") == "ok":
        lines.extend(
            _table(
                ["Measure", "Value"],
                [
                    ["Clean tracks", _count(fp["tracks"])],
                    ["Events fired", _count(fp["events"])],
                    ["Vessel-days observed", _num(fp["vessel_days"], 1)],
                    ["Events per vessel-day", _num(fp["per_vessel_day"], 2)],
                ],
            )
        )
    else:
        lines.append(f"Skipped: {fp.get('reason', 'unknown')}.")

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- The ground truth is our own recorder's view of the ship. A transponder that reports "
            "a wrong position (spoofing or a bad fix) makes the truth wrong too; this harness "
            "cannot separate that from a geometry error.",
            "- Masking a window and asking the prism to cover it tests the physics and the prism, "
            "not whether a real ship that goes dark follows the bridge. The hidden pings lie on "
            "the route the ship actually took, which is the most favourable possible case.",
            "- Speed caps are the working assumptions in core.speeds, not measured service "
            "speeds, and a missing static message means the generic cap is used for that vessel.",
            "- Bridge sigma is fitted on the same masked windows it is then scored on, so the AUC "
            "is optimistic in the statistical sense. It answers 'does the bridge rank the true "
            "path above displaced ones', not 'will it generalise to an unseen gap'.",
            "- Injected gaps are uniform in time, while real AIS outages cluster near ports, "
            "coastlines and busy straits; recall here is an upper bound on real-world recall.",
            "- Detection metrics need the silence/jump/clone rules from core.kinematics and the "
            "receiver coverage series. When either is absent the report says so instead of "
            "printing a zero.",
            "- Tracks are thinned to one ping per interval before masking so a dense recorder "
            "does not produce thousands of hidden fixes per window; the thinning keeps the first "
            "ping after every real outage.",
            "",
            "Data: our own AIS recorder. No Global Fishing Watch data is used in this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write(results: dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(_jsonable(results), indent=1) + "\n")
    text = _render_no_data(results) if results.get("status") == "no data" else _render(results)
    OUT_MD.write_text(text)
    print(f"wrote {OUT_JSON} and {OUT_MD}")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Validate prism/bridge/detector on our own AIS tracks.")
    ap.add_argument("--jsonl-dir", default=None, help="directory of aisstream .jsonl/.jsonl.gz files")
    ap.add_argument("--seed", type=int, default=7, help="fixed seed for every random choice")
    ap.add_argument("--max-vessels", type=int, default=300)
    ap.add_argument("--min-pings", type=int, default=8)
    ap.add_argument("--ping-gap-min", type=float, default=2.0, help="thinning interval in minutes")
    ap.add_argument("--max-cases", type=int, default=250)
    ap.add_argument("--decoys", type=int, default=20)
    ap.add_argument("--max-detect-tracks", type=int, default=200)
    ap.add_argument("--gaps-per-track", type=int, default=3)
    ap.add_argument("--min-gap-min", type=float, default=60.0)
    ap.add_argument("--max-fp-tracks", type=int, default=200)
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    tracks, n_pings, coverage, source = _load(args)
    results = _run(args, tracks, n_pings, coverage, source)
    _write(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
