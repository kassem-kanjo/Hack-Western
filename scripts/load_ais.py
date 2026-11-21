"""Load recorded Baltic AIS into the store.

The recorder writes one gzip JSONL file per UTC hour under ``data/cache/ais/<REGION>/``. The
Baltic engine needs those raw pings (plus static data and receiver coverage) in a queryable
form, so this loader parses the recording once and batches it into whichever repository
``make_repo`` selects (Timescale when ``DATABASE_URL`` is set, else SQLite).

Batches of 5,000 keep memory flat and transactions short on an overnight recording that can
hold millions of messages; re-running is safe because every write is an upsert.

Usage:
    uv run python scripts/load_ais.py --dir data/cache/ais/BALTIC_WATCH --since 2026-11-21T00:00:00

``--since`` is optional and filters *pings* only. Static vessel data is loaded regardless: a
vessel last heard before the window is still a known vessel.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from darkfleet.core.tracks import Ping, VesselInfo
from darkfleet.ingest.ais_tracks import coverage, iter_files
from darkfleet.store.factory import make_repo

DEFAULT_DIR = "data/cache/ais/BALTIC_WATCH"
BATCH_SIZE = 5_000


def _parse_since(value: str) -> datetime:
    """Accept ISO-8601 with or without an offset; a bare timestamp is UTC by convention."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load recorded aisstream JSONL into the store.")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="recording folder or one .jsonl.gz file")
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="ISO timestamp; load only pings at or after it (static data is always loaded)",
    )
    parser.add_argument(
        "--sqlite",
        default="data/cache/darkfleet.sqlite",
        help="SQLite file when no DATABASE_URL is set",
    )
    return parser


def _batches(items: Sequence[Any], size: int = BATCH_SIZE) -> Iterator[Sequence[Any]]:
    """Slice into transaction-sized chunks; a recording must not become one huge transaction."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main(argv: Sequence[str] | None = None, *, repo: Any = None) -> int:
    """Run the load. ``repo`` is the test injection point; the CLI builds one itself."""
    args = build_parser().parse_args(argv)
    directory = Path(args.dir)
    if not directory.exists():
        print(f"--dir does not exist: {directory}", file=sys.stderr)
        return 2

    pings: list[Ping] = []
    vessels: dict[int, VesselInfo] = {}
    for obj in iter_files([directory]):
        if isinstance(obj, Ping):
            if args.since is None or obj.t >= args.since:
                pings.append(obj)
        else:
            # iter_files is time-ordered, so the last report for an MMSI wins.
            vessels[obj.mmsi] = obj

    minutes = coverage(ping.t for ping in pings)
    vessel_rows = list(vessels.values())

    owns_repo = repo is None
    if owns_repo:
        repo = make_repo(sqlite_path=args.sqlite)
    written_pings = written_vessels = written_minutes = 0
    try:
        for batch in _batches(pings):
            written_pings += repo.add_pings(batch)
        for batch in _batches(vessel_rows):
            written_vessels += repo.upsert_vessels(batch)
        for batch in _batches(minutes):
            written_minutes += repo.set_coverage(batch)
        backend = type(repo).__name__
    finally:
        if owns_repo:
            repo.close()

    start = pings[0].t if pings else "-"
    end = pings[-1].t if pings else "-"
    print(f"dir={directory} backend={backend} since={args.since}")
    print(f"pings={written_pings} vessels={written_vessels} coverage_minutes={written_minutes}")
    print(f"window={start} .. {end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
