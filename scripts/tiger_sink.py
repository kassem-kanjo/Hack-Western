"""Stream the recorder's AIS hour files into Tiger Data (TimescaleDB), continuously.

The recorder (``scripts/record_ais.py``, untouched) appends one gzip JSONL file per UTC
hour under ``data/cache/ais/<REGION>/``. This sink tails that directory and writes each
ping's region alongside it, so ``positions`` can answer per-area questions the API needs
(issue #208) without the API re-parsing hour files.

Restart-safe by construction:

* ``data/cache/tiger_sink_offsets.json`` maps each file path to the number of pings already
  sent from it. A restart re-reads every file once, skips those pings, and continues.
* Within a pass, a file is only re-read when its ``mtime`` changed since the last pass, so
  an idle sink does no parsing at all.
* Writes are ``ON CONFLICT (ts, mmsi) DO NOTHING``, so a crash between a write and the
  offsets flush can replay a batch without duplicating rows.

Pings go up in batches of 5,000 per region. One line is logged per pass::

    sent 12000 pings, 3 files

Usage::

    uv run python scripts/tiger_sink.py --ais data/cache/ais --loop 30

``--loop 0`` runs a single pass and exits. ``DATABASE_URL`` is read from ``.env``; with no
URL the sink exits non-zero rather than silently writing somewhere else.

``main(..., repo=fake)`` is the test injection point: no database is opened when a repo is
supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from darkfleet.core.tracks import Ping
from darkfleet.ingest.ais_tracks import iter_files

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_AIS_DIR = "data/cache/ais"
DEFAULT_OFFSETS = "data/cache/tiger_sink_offsets.json"
BATCH_SIZE = 5_000
DEFAULT_LOOP_SECONDS = 0.0


def build_parser() -> argparse.ArgumentParser:
    """CLI wiring; ``repo`` is injected by tests instead of parsed here."""
    parser = argparse.ArgumentParser(description="Stream recorded AIS pings into Tiger Data.")
    parser.add_argument("--ais", default=DEFAULT_AIS_DIR, help="recorder root (<REGION>/*.jsonl.gz)")
    parser.add_argument("--offsets", default=DEFAULT_OFFSETS, help="JSON resume offsets")
    parser.add_argument(
        "--loop",
        type=float,
        default=DEFAULT_LOOP_SECONDS,
        help="seconds between passes; 0 (default) runs one pass",
    )
    return parser


def load_offsets(path: Path) -> dict[str, int]:
    """Read ``path -> pings already sent``; a missing or corrupt file resumes from zero."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    offsets: dict[str, int] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            offsets[key] = value
    return offsets


def save_offsets(path: Path, offsets: dict[str, int]) -> None:
    """Write the offsets atomically: a killed process leaves either old or new, never half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(offsets, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def region_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(region, hour_file)`` for every recorder file, ordered by region then name."""
    try:
        directories = sorted(child for child in root.iterdir() if child.is_dir())
    except OSError:
        return
    for directory in directories:
        for path in sorted(directory.glob("*.jsonl.gz")):
            yield directory.name, path


def _pings(path: Path) -> list[Ping]:
    """Parse one hour file, dropping static-data rows: the sink only writes pings."""
    return [obj for obj in iter_files([path]) if isinstance(obj, Ping)]


def _batches(items: Sequence[Ping], size: int | None = None) -> Iterator[Sequence[Ping]]:
    """Slice into transaction-sized chunks, keeping memory and transactions short."""
    chunk = BATCH_SIZE if size is None else size
    for start in range(0, len(items), chunk):
        yield items[start : start + chunk]


def run_once(
    repo: Any,
    ais_dir: Path,
    offsets_path: Path,
    offsets: dict[str, int] | None = None,
    mtimes: dict[str, int] | None = None,
) -> tuple[int, int]:
    """Make one pass over the recordings. Returns ``(pings sent, files read)``.

    ``offsets`` and ``mtimes`` are owned by the caller so a loop can carry them between
    passes; ``offsets`` is reloaded from disk when omitted. The offsets file is flushed
    after each changed file, so a crash costs at most one file's replay.
    """
    if offsets is None:
        offsets = load_offsets(offsets_path)
    if mtimes is None:
        mtimes = {}

    sent = 0
    files = 0
    for region, path in region_files(ais_dir):
        key = str(path)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            continue
        if mtimes.get(key) == mtime_ns:
            continue

        pings = _pings(path)
        already = offsets.get(key, 0)
        if already > len(pings):
            # The recorder only appends, but a replaced file must not silently hide its
            # first pings: replay from the start and let the (ts, mmsi) key deduplicate.
            already = 0
        new = pings[already:]
        mtimes[key] = mtime_ns

        if already != offsets.get(key, 0):
            offsets[key] = already
            save_offsets(offsets_path, offsets)
        if not new:
            continue

        for batch in _batches(new):
            repo.add_region_pings(region, batch)
        offsets[key] = already + len(new)
        save_offsets(offsets_path, offsets)
        sent += len(new)
        files += 1
    return sent, files


def main(argv: Sequence[str] | None = None, *, repo: Any = None) -> int:
    """Run the sink. ``repo`` is the test injection point; the CLI builds a Tiger repo itself."""
    args = build_parser().parse_args(argv)
    ais_dir = Path(args.ais)
    offsets_path = Path(args.offsets)
    if not ais_dir.is_dir():
        print(f"--ais does not exist: {ais_dir}", file=sys.stderr)
        return 2

    owns_repo = repo is None
    if owns_repo:
        load_dotenv(ROOT / ".env")
        url = os.environ.get("DATABASE_URL", "").strip()
        if not url:
            print("DATABASE_URL is not set (copy .env.example to .env and fill it in)", file=sys.stderr)
            return 1
        from darkfleet.store.timescale_repo import TimescaleRepo

        repo = TimescaleRepo(url)

    offsets = load_offsets(offsets_path)
    mtimes: dict[str, int] = {}
    try:
        while True:
            sent, files = run_once(repo, ais_dir, offsets_path, offsets, mtimes)
            print(f"sent {sent} pings, {files} files", flush=True)
            if args.loop <= 0:
                break
            time.sleep(args.loop)
    except KeyboardInterrupt:
        return 0
    finally:
        if owns_repo:
            repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
