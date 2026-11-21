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
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from darkfleet.store.factory import make_repo
from darkfleet.store.repository import Repository
from darkfleet.store.sqlite_repo import SqliteRepo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "snapshot" / "stats.json"
DEFAULT_SQLITE = ROOT / "data" / "cache" / "darkfleet.sqlite"
DEFAULT_DAYS = 30


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write daily dark hours to data/snapshot/stats.json.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="size of the window in days")
    parser.add_argument("--start", type=date.fromisoformat, help="first UTC day, inclusive")
    parser.add_argument("--end", type=date.fromisoformat, help="last UTC day, inclusive (default: today UTC)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="snapshot path")
    parser.add_argument(
        "--sqlite", type=Path, default=DEFAULT_SQLITE, help="SQLite file when no DATABASE_URL"
    )
    return parser


def build_snapshot(repo: Repository, start: date, end: date) -> dict[str, Any]:
    """Shape the aggregates into the snapshot the site serves.

    The old ``dark_hours_daily`` / ``validation`` keys keep their shape. ``sar_weekly``
    and ``ais_hourly`` sit next to them; the hourly series is the last 48 buckets that
    fall inside ``[start, end]``, ending at the latest hour present in the data.
    ``source`` names the backend that answered.
    """
    buckets = repo.dark_hours_daily(start, end, refresh=True)
    weeks = repo.sar_weekly(start, end)
    window_start = datetime.combine(start, time.min, tzinfo=UTC)
    window_end = datetime.combine(end, time.min, tzinfo=UTC) + timedelta(days=1)
    hours = repo.ais_hourly(window_start, window_end)[-48:]
    return {
        "dark_hours_daily": [
            {"day": bucket["day"].isoformat(), "hours": round(bucket["hours"], 3), "gaps": bucket["gaps"]}
            for bucket in buckets
        ],
        "sar_weekly": [
            {
                "week": week["week"].isoformat(),
                "region": week["region"],
                "seen": week["seen"],
                "broadcasting": week["broadcasting"],
                "dark": week["dark"],
            }
            for week in weeks
        ],
        "ais_hourly": [{"hour": _iso_hour(bucket["hour"]), "msgs": bucket["msgs"]} for bucket in hours],
        "source": "sqlite" if isinstance(repo, SqliteRepo) else "tiger",
        "validation": None,
    }


def _iso_hour(value: datetime) -> str:
    """UTC bucket start as the same ``...Z`` form the rest of the API uses."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
