"""Build the unseen-fleet snapshot: radar detections vs AIS broadcasts in one region.

    uv run python scripts/build_unseen_fleet.py --start 2026-08-15 --end 2026-11-15 --region HORMUZ

For every UTC day in the window the snapshot answers two fleet-level questions: how many
vessels radar saw (matched plus unmatched detections) and how many of those were also
broadcasting AIS. It never names or scores an individual ship. Global Fishing Watch caps
a 4Wings report at a short window, so the request is split into <= 31-day chunks; the
client caches every response on disk, which makes a rerun free and offline.

``--to-db`` also writes the daily series into ``sar_daily`` (a Tiger/Timescale
hypertable) through the repository, which keeps a ``sar_weekly`` continuous aggregate
warm. The GFW token is read from ``.env`` / the environment at runtime and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from darkfleet.core.unseen import daily_counts, density_grid, summary
from darkfleet.ingest.gfw import GfwClient, SarCellRow
from darkfleet.ingest.regions import REGIONS, region_geometry
from darkfleet.store.factory import make_repo
from darkfleet.store.repository import SarDailyRow

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "snapshot" / "hormuz_unseen.json"
DEFAULT_CACHE = "data/cache/gfw"
DEFAULT_SQLITE = "data/cache/darkfleet.sqlite"

SOURCE = "Global Fishing Watch SAR vessel detections (Sentinel-1)"
CAVEATS = [
    "Radar misses vessels under ~25 m",
    "'Dark' means no AIS match, not wrongdoing",
    "Daily resolution",
]
MAX_CHUNK_DAYS = 31


def date_chunks(start: date, end: date, max_days: int = MAX_CHUNK_DAYS) -> Iterator[tuple[date, date]]:
    """Yield inclusive chunks of at most ``max_days`` calendar days.

    The GFW report endpoint rejects long windows, and an inclusive day range of 31 days
    spans exactly 31 dates (chunk_end = start + 30 days).
    """
    if max_days < 1:
        raise ValueError("max_days must be at least 1")
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=max_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def fetch_series(
    client: Any,
    geometry: dict,
    start: date,
    end: date,
) -> tuple[list[SarCellRow], list[SarCellRow]]:
    """Fetch matched (AIS-broadcasting) and unmatched (dark) SAR rows for a window."""
    matched: list[SarCellRow] = []
    unmatched: list[SarCellRow] = []
    for chunk_start, chunk_end in date_chunks(start, end):
        matched.extend(client.sar_cells(geometry, chunk_start, chunk_end, matched=True))
        unmatched.extend(client.sar_cells(geometry, chunk_start, chunk_end, matched=False))
    return matched, unmatched


def build_snapshot(client: Any, region: str, start: date, end: date) -> dict[str, Any]:
    """Compute the snapshot payload from a GFW client (real or injected fake)."""
    geometry = region_geometry(region)
    matched, unmatched = fetch_series(client, geometry, start, end)
    daily = daily_counts(matched, unmatched)
    return {
        "region": region,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "daily": daily,
        "grid": density_grid(unmatched),
        "summary": summary(daily),
        "source": SOURCE,
        "caveats": list(CAVEATS),
    }


def _daily_rows(payload: dict[str, Any]) -> list[SarDailyRow]:
    """Turn the JSON daily series back into typed rows for the store."""
    region = str(payload["region"])
    return [
        SarDailyRow(
            day=date.fromisoformat(str(row["day"])),
            region=region,
            seen=int(row["seen"]),
            broadcasting=int(row["broadcasting"]),
            dark=int(row["dark"]),
        )
        for row in payload["daily"]
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the unseen-fleet radar snapshot.")
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--region", default="HORMUZ", choices=sorted(REGIONS), help="named region")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot JSON path")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE, help="GFW response cache folder")
    parser.add_argument(
        "--to-db",
        action="store_true",
        help="also upsert the daily series into sar_daily (Timescale when DATABASE_URL is set)",
    )
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE, help="SQLite file when no DATABASE_URL")
    return parser


def main(argv: Sequence[str] | None = None, *, client: Any = None, repo: Any = None) -> int:
    """Run the build. ``client``/``repo`` are injection points for offline tests."""
    args = build_parser().parse_args(argv)
    if args.end < args.start:
        print("--end is before --start", file=sys.stderr)
        return 2

    if client is None:
        load_dotenv(ROOT / ".env")
        token = os.environ.get("GFW_API_TOKEN", "")
        if not token:
            print("GFW_API_TOKEN is not set (copy .env.example to .env and fill it in)", file=sys.stderr)
            return 2
        client = GfwClient(token, cache_dir=args.cache_dir)

    payload = build_snapshot(client, args.region, args.start, args.end)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    headline = payload["summary"]
    print(
        f"region={args.region} start={args.start} end={args.end} "
        f"days={headline['days']} seen={headline['seen_total']} dark={headline['dark_total']} "
        f"dark_share_mean={headline['dark_share_mean']:.3f}"
    )
    print(f"wrote {args.out}")

    if args.to_db:
        owns_repo = repo is None
        if owns_repo:
            repo = make_repo(sqlite_path=args.sqlite)
        try:
            written = repo.upsert_sar_daily(_daily_rows(payload))
            backend = type(repo).__name__
        finally:
            if owns_repo:
                repo.close()
        print(f"sar_daily written={written} backend={backend}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
