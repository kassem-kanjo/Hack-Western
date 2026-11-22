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

In loop mode a failed pass (database restart, network blip, bad batch) is logged as one line,
the connection is closed and reopened, and the loop waits 5 s, doubling to a 60 s cap, before
the next try. The loop never exits on an error: the live demo reads the sink's output.

``main(..., repo=fake)`` is the test injection point: no database is opened when a repo is
supplied. ``repo_factory`` lets a test hand out a fresh repo after a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
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
RETRY_FIRST_SECONDS = 5.0
RETRY_CAP_SECONDS = 60.0


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

        if already != offsets.get(key, 0):
            offsets[key] = already
            save_offsets(offsets_path, offsets)
        if not new:
            # Nothing to send, but remember the mtime so the file is not re-parsed each pass.
            mtimes[key] = mtime_ns
            continue

        # The mtime is recorded only after the writes succeed: if a batch raises, the next
        # pass must read this file again instead of skipping its unsent pings.
        for batch in _batches(new):
            repo.add_region_pings(region, batch)
        offsets[key] = already + len(new)
        save_offsets(offsets_path, offsets)
        mtimes[key] = mtime_ns
        sent += len(new)
        files += 1
    return sent, files


def _retry_delay(failures: int) -> float:
    """Seconds to wait after the ``failures``-th failure in a row: 5, 10, 20, 40, then 60."""
    return min(RETRY_CAP_SECONDS, RETRY_FIRST_SECONDS * (2 ** max(0, failures - 1)))


def _close_quietly(repo: Any) -> None:
    """Close a connection that may already be dead; a failing close must not stop a retry."""
    close = getattr(repo, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _report_failure(exc: BaseException) -> str:
    """One log line for a failed pass: the exception type and its first line, never a URL."""
    detail = str(exc).splitlines()[0][:200] if str(exc) else ""
    return (
        f"pass failed ({type(exc).__name__}: {detail})" if detail else f"pass failed ({type(exc).__name__})"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    repo: Any = None,
    repo_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the sink. ``repo`` and ``repo_factory`` are the test injection points.

    The CLI builds Tiger repos itself and closes them on exit. With an injected ``repo`` and no
    factory, a failed pass reuses that same object (closed first if it has ``close``).
    """
    args = build_parser().parse_args(argv)
    ais_dir = Path(args.ais)
    offsets_path = Path(args.offsets)
    if not ais_dir.is_dir():
        print(f"--ais does not exist: {ais_dir}", file=sys.stderr)
        return 2

    owns_repo = repo is None
    if repo_factory is None and owns_repo:
        load_dotenv(ROOT / ".env")
        url = os.environ.get("DATABASE_URL", "").strip()
        if not url:
            print("DATABASE_URL is not set (copy .env.example to .env and fill it in)", file=sys.stderr)
            return 1
        from darkfleet.store.timescale_repo import TimescaleRepo

        repo_factory = lambda: TimescaleRepo(url)  # noqa: E731
    elif repo_factory is None:
        repo_factory = lambda: repo  # noqa: E731
    assert repo_factory is not None

    offsets = load_offsets(offsets_path)
    mtimes: dict[str, int] = {}
    failures = 0
    try:
        while True:
            try:
                if repo is None:
                    repo = repo_factory()
                sent, files = run_once(repo, ais_dir, offsets_path, offsets, mtimes)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive any pass failure
                failures += 1
                if args.loop <= 0:
                    print(_report_failure(exc), file=sys.stderr, flush=True)
                    return 1
                delay = _retry_delay(failures)
                print(f"{_report_failure(exc)}; reconnecting in {delay:g}s", file=sys.stderr, flush=True)
                if repo is not None:
                    _close_quietly(repo)
                repo = None
                sleep(delay)
                continue

            failures = 0
            print(f"sent {sent} pings, {files} files", flush=True)
            if args.loop <= 0:
                break
            sleep(args.loop)
    except KeyboardInterrupt:
        return 0
    finally:
        if owns_repo and repo is not None:
            _close_quietly(repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
