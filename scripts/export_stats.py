"""Export the daily dark-hours aggregate to ``data/snapshot/stats.json``.

The web app reads this snapshot, so it never needs credentials or a live database.
The backend comes from ``make_repo()``: Timescale when ``DATABASE_URL`` is set, the
SQLite cache otherwise.

Example:

    uv run python scripts/export_stats.py --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from darkfleet.store.factory import make_repo
from darkfleet.store.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "snapshot" / "stats.json"
DEFAULT_SQLITE = ROOT / "data" / "cache" / "darkfleet.sqlite"
DEFAULT_DAYS = 30


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write daily dark hours to data/snapshot/stats.json."
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="size of the window in days")
    parser.add_argument("--start", type=date.fromisoformat, help="first UTC day, inclusive")
    parser.add_argument(
        "--end", type=date.fromisoformat, help="last UTC day, inclusive (default: today UTC)"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="snapshot path")
    parser.add_argument(
        "--sqlite", type=Path, default=DEFAULT_SQLITE, help="SQLite file when no DATABASE_URL"
    )
    return parser


def build_snapshot(repo: Repository, start: date, end: date) -> dict[str, Any]:
    """Shape the aggregate into the snapshot the site serves."""
    buckets = repo.dark_hours_daily(start, end, refresh=True)
    return {
        "dark_hours_daily": [
            {"day": bucket["day"].isoformat(), "hours": round(bucket["hours"], 3), "gaps": bucket["gaps"]}
            for bucket in buckets
        ],
        "validation": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    end = args.end or datetime.now(UTC).date()
    if args.start is not None:
        start = args.start
    else:
        start = end - timedelta(days=max(args.days, 1) - 1)
    if end < start:
        print("--end is before --start", file=sys.stderr)
        return 2

    repo = make_repo(sqlite_path=args.sqlite)
    try:
        snapshot = build_snapshot(repo, start, end)
    finally:
        repo.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    days = len(snapshot["dark_hours_daily"])
    print(f"wrote {args.output} days={days} start={start} end={end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
