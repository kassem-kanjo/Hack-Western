"""Fetch one region's Global Fishing Watch gaps, encounters and SAR cells into the store.

Example (the integration check for the Strait of Hormuz, October 2026):

    uv run python scripts/ingest_region.py --region HORMUZ --start 2026-10-01 --end 2026-10-31

The GFW token is read from ``.env`` (``GFW_API_TOKEN``) and is only sent in the Authorization header.
Responses are cached under ``data/cache/gfw``, so re-running the same window replays from disk
and makes no new API calls. The store is Timescale when ``DATABASE_URL`` is set, else SQLite.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from darkfleet.ingest.regions import REGIONS, region_geometry
from darkfleet.store.factory import make_repo

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest one region from Global Fishing Watch into the store."
    )
    parser.add_argument("--region", required=True, choices=sorted(REGIONS), help="named region")
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--cache-dir", default="data/cache/gfw", help="raw response cache folder")
    parser.add_argument(
        "--sqlite", default="data/cache/darkfleet.sqlite", help="SQLite file when no DATABASE_URL"
    )
    return parser


def main(argv: Sequence[str] | None = None, *, client: Any = None, repo: Any = None) -> int:
    """Run the ingest. ``client`` and ``repo`` are injection points for tests; the CLI leaves them None."""
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
        from darkfleet.ingest.gfw import GfwClient

        client = GfwClient(token, cache_dir=args.cache_dir)

    geometry = region_geometry(args.region)
    gaps = client.gaps(geometry, args.start, args.end)
    encounters = client.encounters(geometry, args.start, args.end)
    cells = client.sar_cells(geometry, args.start, args.end, unmatched_only=True)

    owns_repo = repo is None
    if owns_repo:
        repo = make_repo(sqlite_path=args.sqlite)
    try:
        written_gaps = repo.upsert_gaps(args.region, gaps)
        written_enc = repo.upsert_encounters(args.region, encounters)
        written_sar = repo.upsert_sar_cells(args.region, cells)
        daily = repo.dark_hours_daily(args.region)
        backend = type(repo).__name__
    finally:
        if owns_repo:
            repo.close()

    print(f"region={args.region} start={args.start} end={args.end} backend={backend}")
    print(f"gaps fetched={len(gaps)} written={written_gaps}")
    print(f"encounters fetched={len(encounters)} written={written_enc}")
    print(f"sar_cells_unmatched fetched={len(cells)} written={written_sar}")
    print(f"dark_hours_days={len(daily)} dark_hours_total={sum(d.dark_hours for d in daily):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
