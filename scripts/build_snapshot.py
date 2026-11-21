"""Build the read-only API snapshot from the case export.

    uv run python scripts/build_snapshot.py [--gaps-scanned 5624] [--counts data/cases/rerank_counts.json]

The API only ever reads data/snapshot/, so this is the single place where the
re-ranked case export becomes the frozen v1 shapes. When the re-rank counts table
is present, its baseline gap count and the case statuses drive the funnel totals;
without it the builder keeps the old post-rerank totals.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from darkfleet.core.scoring import score_case

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "cases" / "cases_v2.json"
DEFAULT_COUNTS = ROOT / "data" / "cases" / "rerank_counts.json"
DEFAULT_OUT = ROOT / "data" / "snapshot"


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
) -> dict[str, Any]:
    """Write both snapshot files and return the totals for the caller to print."""
    cases, generated_at = _read_cases(source)
    cases = _score_cases(cases)
    totals = compute_totals(cases, gaps_scanned, _read_counts(counts_path))
    payload = {
        "generated_at": generated_at
        or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "totals": totals,
        "cases": cases,
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "cases.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out / "stats.json").write_text(
        json.dumps({"dark_hours_daily": [], "validation": None}, indent=2) + "\n",
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
    args = parser.parse_args()

    if not args.source.is_file():
        parser.error(f"missing cases export: {args.source}")

    totals = build(args.source, args.out, args.gaps_scanned, args.counts)
    used = args.counts if args.counts.is_file() else "none (legacy totals)"
    print(f"wrote {args.out / 'cases.json'} and {args.out / 'stats.json'} from counts {used}: {totals}")


if __name__ == "__main__":
    main()
