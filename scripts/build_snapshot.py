"""Build the read-only API snapshot from the case export.

    uv run python scripts/build_snapshot.py [--gaps-scanned 5624]

The API only ever reads data/snapshot/, so this is the single place where the
re-ranked case export becomes the frozen v1 shapes.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "cases" / "cases_v2.json"
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


def compute_totals(cases: list[dict[str, Any]], gaps_scanned: int) -> dict[str, int]:
    """Headline funnel numbers; the UI shows them above the case list."""
    with_candidates = sum(
        1 for case in cases if case.get("status") in {"candidate", "ruled_out"} and _had_candidates(case)
    )
    return {
        "gaps_scanned": gaps_scanned,
        "with_candidates": with_candidates,
        "ruled_out": sum(1 for case in cases if case.get("status") == "ruled_out"),
        "meetings": sum(_n_meetings(case) for case in cases),
    }


def build(source: Path, out: Path, gaps_scanned: int) -> dict[str, int]:
    """Write both snapshot files and return the totals for the caller to print."""
    cases, generated_at = _read_cases(source)
    totals = compute_totals(cases, gaps_scanned)
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
    args = parser.parse_args()

    if not args.source.is_file():
        parser.error(f"missing cases export: {args.source}")

    totals = build(args.source, args.out, args.gaps_scanned)
    print(f"wrote {args.out / 'cases.json'} and {args.out / 'stats.json'}: {totals}")


if __name__ == "__main__":
    main()
