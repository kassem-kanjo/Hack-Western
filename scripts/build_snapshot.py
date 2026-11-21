"""Build the read-only API snapshot from the case export.

    uv run python scripts/build_snapshot.py [--gaps-scanned 5624]
        [--counts data/cases/rerank_counts.json]
        [--land web/public/basemap/land.geojson]

The API only ever reads data/snapshot/, so this is the single place where the
re-ranked case export becomes the frozen v1 shapes. When the re-rank counts table
is present, its baseline gap count and the case statuses drive the funnel totals;
without it the builder keeps the old post-rerank totals. Every lens frame also gains
the bridge's k-sigma "likely" contour (clipped to that frame's lens), and stats.json
gains the validation block read from data/validation/ (nulls when files are missing).
When a land polygon is available every footprint, lens and likely zone is clipped to
water, and each case gains the matching caveat.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely.errors import GEOSException
from shapely.geometry import Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry

from darkfleet.core.bridge import bridge_ellipse
from darkfleet.core.geo import Fix
from darkfleet.core.scoring import score_case
from darkfleet.core.water import clip_to_water, load_land

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "cases" / "cases_v2.json"
DEFAULT_COUNTS = ROOT / "data" / "cases" / "rerank_counts.json"
DEFAULT_VALIDATION = ROOT / "data" / "validation"
DEFAULT_LAND = ROOT / "web" / "public" / "basemap" / "land.geojson"
DEFAULT_OUT = ROOT / "data" / "snapshot"

WATER_CAVEAT = (
    "Zones are clipped to water using a coarse 50 m coastline; the validation tests used unclipped zones."
)

# The bridge's fitted drift scale (data/validation/bridge_results.json). The fallback
# is the value recorded in docs/validation.md, so a missing file degrades the shaded
# region slightly rather than dropping it.
DEFAULT_SIGMA_KM_PER_SQRT_H = 21.62
LIKELY_K = 2.0

VALIDATION_VERDICT = "Radar near dark ships is not better than chance; the bridge locates ships (AUC 0.87)."
VALIDATION_SOURCE = "docs/validation.md"


def _read_cases(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Accept either the raw list export or the wrapped {generated_at, cases} form."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        cases = payload.get("cases", [])
        if not isinstance(cases, list):
            raise ValueError("'cases' must be a list")
        generated_at = payload.get("generated_at")
        return cases, generated_at if isinstance(generated_at, str) else None
    raise ValueError(f"unsupported cases export: {type(payload).__name__}")


def _score_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the analyst-attention score to every case, then rank by it."""
    scored: list[dict[str, Any]] = []
    for case in cases:
        scored_case = dict(case)
        scored_case.update(score_case(scored_case))
        scored.append(scored_case)
    scored.sort(key=lambda case: case["score"], reverse=True)
    return scored


def _had_candidates(case: dict[str, Any]) -> bool:
    """Treat either the precomputed count or the raw candidate list as evidence."""
    count = case.get("n_candidates")
    if count is None:
        candidates = case.get("candidates")
        count = len(candidates) if isinstance(candidates, list) else 0
    return count > 0


def _n_meetings(case: dict[str, Any]) -> int:
    """Meetings may arrive as a count or as the list itself."""
    count = case.get("n_meetings")
    if count is None:
        meetings = case.get("meetings")
        count = len(meetings) if isinstance(meetings, list) else 0
    return count


def _read_counts(path: Path | None) -> dict[str, int] | None:
    """Read the re-rank counts table; a missing file means keep the legacy totals."""
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"counts file must be an object: {path}")
    baseline = payload.get("baseline")
    if not isinstance(baseline, dict):
        raise ValueError(f"counts file has no baseline block: {path}")
    gaps = baseline.get("gaps_with_candidates", baseline.get("gaps"))
    if not isinstance(gaps, int) or isinstance(gaps, bool):
        raise ValueError(f"counts baseline has no integer gap count: {path}")
    return {"baseline_gaps": gaps}


def _read_json(path: Path | None) -> dict[str, Any]:
    """Read one validation JSON; a missing or malformed file is simply an empty block."""
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _lookup(payload: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def _num(payload: Any, *keys: str) -> float | None:
    """Walk a nested key path and return a plain float, or None if absent/not numeric."""
    value = _lookup(payload, *keys)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _interval(payload: Any, lo: tuple[str, ...], hi: tuple[str, ...]) -> list[float | None] | None:
    """A [lo, hi] pair, or None when either end is missing (never a half-interval)."""
    low, high = _num(payload, *lo), _num(payload, *hi)
    return [low, high] if low is not None and high is not None else None


def read_sigma(directory: Path | None = DEFAULT_VALIDATION) -> float:
    """Fitted bridge sigma, or the documented default when the validation file is absent."""
    path = directory / "bridge_results.json" if directory else None
    value = _num(_read_json(path), "sigma_km_per_sqrt_h")
    return value if value is not None and value > 0.0 else DEFAULT_SIGMA_KM_PER_SQRT_H


def validation_stats(directory: Path | None = DEFAULT_VALIDATION) -> dict[str, Any]:
    """The stats.json validation block, read from the three pre-registered result files.

    Every number is optional: a missing or half-written result file leaves its fields
    null instead of failing the snapshot build. The verdict and source are static
    because they restate docs/validation.md, not a single run.
    """
    results = _read_json((directory / "results.json") if directory else None)
    bridge = _read_json((directory / "bridge_results.json") if directory else None)
    holdout = _read_json((directory / "holdout_results.json") if directory else None)

    primary = ("null_test", "variants", "primary")
    holdout_primary = ("primary",)
    return {
        "null_ratio": _num(results, *primary, "ratio"),
        "null_ci": _interval(results, (*primary, "ratio_lo"), (*primary, "ratio_hi")),
        "null_p": _num(results, *primary, "p_value"),
        "bridge_auc": _num(bridge, "d_auc", "auc"),
        "bridge_auc_ci": _interval(bridge, ("d_auc", "lo"), ("d_auc", "hi")),
        "bridge_radar_ratio": _num(bridge, "c_exceed", "ratio"),
        "holdout_ratio": _num(holdout, *holdout_primary, "ratio"),
        "holdout_ci": _interval(holdout, (*holdout_primary, "ratio_lo"), (*holdout_primary, "ratio_hi")),
        "verdict": VALIDATION_VERDICT,
        "source": VALIDATION_SOURCE,
    }


def _parse_t(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _position(value: Any) -> tuple[float, float] | None:
    """A [lon, lat] pair, or None if the value is not a two-number sequence."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lon, lat = value
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lon, lat)):
        return None
    return float(lon), float(lat)


def _fixes(case: dict[str, Any]) -> tuple[Fix, Fix] | None:
    """Rebuild the gap endpoints from the case summary; None when the case is incomplete."""
    off = case.get("off")
    on = case.get("on")
    if not isinstance(off, dict) or not isinstance(on, dict):
        return None
    t0, t1 = _parse_t(off.get("t")), _parse_t(on.get("t"))
    p0, p1 = _position(off.get("pos")), _position(on.get("pos"))
    if t0 is None or t1 is None or p0 is None or p1 is None:
        return None
    return Fix(p0[1], p0[0], t0), Fix(p1[1], p1[0], t1)


def _speed_kn(case: dict[str, Any]) -> float | None:
    value = case.get("speed_cap_kn")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _polygon_json(poly: Polygon) -> dict[str, Any] | None:
    """GeoJSON Polygon at ~0.1 m precision; None for an empty shape so JSON stays valid."""
    if poly.is_empty:
        return None
    ring = [[round(float(x), 6), round(float(y), 6)] for x, y in poly.exterior.coords]
    return {"type": "Polygon", "coordinates": [ring]}


def _likely(fixes: tuple[Fix, Fix] | None, sigma: float, v_kn: float | None, when: Any) -> Any:
    """The k-sigma bridge contour for one frame, clipped to the lens when the cap is known."""
    t = _parse_t(when)
    if fixes is None or t is None:
        return None
    try:
        poly = bridge_ellipse(*fixes, sigma, t, v_kn=v_kn, k=LIKELY_K)
    except (ValueError, TypeError, ArithmeticError, GEOSException):
        return None  # one bad frame must not cost the whole snapshot
    return _polygon_json(poly)


def add_likely_contours(case: dict[str, Any], sigma: float) -> None:
    """Attach the "most likely" region to every lens frame of one case, in place."""
    frames = case.get("lens_frames")
    if not isinstance(frames, list):
        return
    fixes = _fixes(case)
    v_kn = _speed_kn(case)
    for frame in frames:
        if isinstance(frame, dict):
            frame["likely"] = _likely(fixes, sigma, v_kn, frame.get("t"))


def _clip_geojson(value: Any, land: BaseGeometry) -> dict[str, Any] | None:
    """Clip one GeoJSON geometry to water; None for an empty or unusable shape."""
    if not isinstance(value, dict):
        return None
    try:
        geom = shape(value)
    except (AttributeError, KeyError, TypeError, ValueError, GEOSException):
        return None
    clipped = clip_to_water(geom, land)
    return None if clipped.is_empty else mapping(clipped)


def clip_case_to_water(case: dict[str, Any], land: BaseGeometry) -> None:
    """Clip footprint and every frame's lens/likely zone to water, in place.

    A MultiPolygon is allowed: a lens crossing a headland legitimately becomes two
    pieces. The caveat is added exactly once per case.
    """
    if "footprint" in case:
        case["footprint"] = _clip_geojson(case.get("footprint"), land)
    frames = case.get("lens_frames")
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            for key in ("polygon", "likely"):
                if key in frame:
                    frame[key] = _clip_geojson(frame.get(key), land)
    caveats = case.get("caveats")
    if not isinstance(caveats, list):
        caveats = []
        case["caveats"] = caveats
    if WATER_CAVEAT not in caveats:
        caveats.append(WATER_CAVEAT)


def compute_totals(
    cases: list[dict[str, Any]],
    gaps_scanned: int,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Headline funnel numbers; the UI shows them above the case list."""
    ruled_out = sum(1 for case in cases if case.get("status") == "ruled_out")
    meetings = sum(_n_meetings(case) for case in cases)
    if counts is None:
        with_candidates = sum(
            1 for case in cases if case.get("status") in {"candidate", "ruled_out"} and _had_candidates(case)
        )
        return {
            "gaps_scanned": gaps_scanned,
            "with_candidates": with_candidates,
            "ruled_out": ruled_out,
            "meetings": meetings,
        }

    # The baseline gap count comes from the counts table (109 before the filters);
    # only the survivors are cases, so the old post-rerank sum would give 19.
    baseline_gaps = counts["baseline_gaps"]
    candidates_final = sum(1 for case in cases if case.get("status") == "candidate")
    return {
        "gaps_scanned": gaps_scanned,
        "with_candidates": baseline_gaps,
        "ruled_out": ruled_out,
        "candidates_final": candidates_final,
        "meetings": meetings,
        "funnel": [
            {"stage": "scanned", "label": "Gaps scanned", "count": gaps_scanned},
            {"stage": "radar", "label": "Radar inside prism", "count": baseline_gaps},
            {
                "stage": "unexplained",
                "label": "Unexplained after physics checks",
                "count": candidates_final,
            },
        ],
    }


def build(
    source: Path,
    out: Path,
    gaps_scanned: int,
    counts_path: Path | None = DEFAULT_COUNTS,
    validation_dir: Path | None = DEFAULT_VALIDATION,
    land: Path | None = None,
) -> dict[str, Any]:
    """Write both snapshot files and return the totals for the caller to print.

    `land` is optional so programmatic callers that do not need coastal clipping keep
    the historical behaviour; the CLI passes the default Natural Earth file.
    """
    cases, generated_at = _read_cases(source)
    cases = _score_cases(cases)
    totals = compute_totals(cases, gaps_scanned, _read_counts(counts_path))
    sigma = read_sigma(validation_dir)
    land_geom = load_land(land) if land is not None and Path(land).is_file() else None
    for case in cases:
        add_likely_contours(case, sigma)
        if land_geom is not None:
            clip_case_to_water(case, land_geom)
    payload = {
        "generated_at": generated_at
        or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "totals": totals,
        "cases": cases,
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "cases.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out / "stats.json").write_text(
        json.dumps({"dark_hours_daily": [], "validation": validation_stats(validation_dir)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return totals


def main() -> None:
    """Parse CLI flags, fail loudly on a missing export, and report the funnel."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gaps-scanned", type=int, default=5624)
    parser.add_argument("--counts", type=Path, default=DEFAULT_COUNTS)
    parser.add_argument("--land", type=Path, default=DEFAULT_LAND)
    args = parser.parse_args()

    if not args.source.is_file():
        parser.error(f"missing cases export: {args.source}")

    totals = build(args.source, args.out, args.gaps_scanned, args.counts, land=args.land)
    used = args.counts if args.counts.is_file() else "none (legacy totals)"
    print(f"wrote {args.out / 'cases.json'} and {args.out / 'stats.json'} from counts {used}: {totals}")


if __name__ == "__main__":
    main()
