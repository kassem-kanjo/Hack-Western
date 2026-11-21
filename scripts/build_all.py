"""Rebuild every demo dataset in one command.

    uv run python scripts/build_all.py [--regions BALTIC_WATCH,MALAYSIA_EOPL,BLACK_SEA]
        [--data-dir data] [--start 2026-08-15 --end 2026-11-15]

The demo is only trustworthy when every snapshot file comes from the same run. This script walks
the builders in dependency order, imports them and calls their ``main(...)`` in-process (no shelling
out), then writes ``data/snapshot/manifest.json`` describing what was produced and prints a table.

Order:

1. ``build_snapshot`` (global cases) when ``data/cases/cases_v2.json`` exists;
2. ``build_live`` for every ``--regions`` entry whose ``data/cache/ais/<REGION>`` folder exists;
3. ``build_unseen_fleet`` and ``build_hormuz_history`` for the Hormuz window.

The two GFW builders are offline-friendly: their clients replay cached responses from
``data/cache/gfw``, so no network is needed when the cache is warm. If a builder exits because
``GFW_API_TOKEN`` is missing and an output file already exists, the existing file is kept and the
run says so (``--offline-ok``, on by default).

Exit status is 1 when a required dataset (``cases.json``, ``stats.json``, at least one
``live_*.json``, ``hormuz_unseen.json``) is missing or empty; the manifest is still written so a
failed run is diagnosable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data"
DEFAULT_REGIONS = "BALTIC_WATCH,MALAYSIA_EOPL,BLACK_SEA"
# The window documented by the two Hormuz builders; reused when no output exists yet.
DEFAULT_START = date(2026, 8, 15)
DEFAULT_END = date(2026, 11, 15)
MANIFEST = "manifest.json"
REQUIRED = ("cases.json", "stats.json", "hormuz_unseen.json")


def _load_script(name: str) -> ModuleType:
    """Import a sibling script by path so build_all works from any working directory."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_build_all_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load builder module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_snapshot = _load_script("build_snapshot")
build_live = _load_script("build_live")
build_unseen_fleet = _load_script("build_unseen_fleet")
build_hormuz_history = _load_script("build_hormuz_history")


def _code(result: Any) -> int:
    """Builders either return an int or (build_snapshot) return None on success."""
    return 0 if result is None else int(result)


def _call(function: Any, *args: Any) -> int:
    """Call a builder and turn argparse's SystemExit into a status code."""
    try:
        return _code(function(*args))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


@contextmanager
def _argv(args: Sequence[str]) -> Iterator[None]:
    """Give build_snapshot (which has no argv parameter) temporary command-line flags."""
    previous = sys.argv
    sys.argv = ["build_snapshot.py", *args]
    try:
        yield
    finally:
        sys.argv = previous


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _land() -> Path:
    return ROOT / "web" / "public" / "basemap" / "land.geojson"


def _snapshot_files(out_dir: Path) -> list[Path]:
    """Every dataset in the snapshot folder, in a stable, human-friendly order."""
    files = [out_dir / "cases.json", out_dir / "stats.json"]
    files.extend(sorted(out_dir.glob("live_*.json")))
    files.extend([out_dir / "hormuz_unseen.json", out_dir / "hormuz_history.json"])
    return [path for path in files if path.is_file()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a sibling ``.tmp`` file so readers never see a partial manifest.

    Leftover ``.tmp`` files from a crashed run are harmless: the next successful build
    replaces the target atomically, and the API only ever reads the final name.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _present(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _window(data_dir: Path, start: date | None, end: date | None) -> tuple[date, date]:
    """Reuse the window recorded by an earlier Hormuz file, else the documented default."""
    if start is not None and end is not None:
        return start, end
    out_dir = data_dir / "snapshot"
    for name in ("hormuz_unseen.json", "hormuz_history.json"):
        period = _read_payload(out_dir / name).get("period")
        if not isinstance(period, dict):
            continue
        try:
            return date.fromisoformat(str(period["start"])), date.fromisoformat(str(period["end"]))
        except (KeyError, ValueError):
            continue
    return DEFAULT_START, DEFAULT_END


def _run_snapshot(data_dir: Path, out_dir: Path) -> None:
    source = data_dir / "cases" / "cases_v2.json"
    if not source.is_file():
        warn(f"build_snapshot skipped: no cases export at {source}")
        return
    args = [
        "--source",
        str(source),
        "--out",
        str(out_dir),
        "--counts",
        str(data_dir / "cases" / "rerank_counts.json"),
        "--land",
        str(_land()),
        "--sanctions",
        str(data_dir / "sanctions" / "maritime.csv"),
    ]
    with _argv(args):
        code = _call(build_snapshot.main)
    if code != 0:
        warn(f"build_snapshot exited {code}")


def _run_live(region: str, data_dir: Path, out_dir: Path) -> None:
    directory = data_dir / "cache" / "ais" / region
    if not directory.is_dir():
        warn(f"build_live skipped {region}: no recording at {directory}")
        return
    code = _call(
        build_live.main,
        [
            "--region",
            str(directory),
            "--out",
            str(out_dir),
            "--land",
            str(_land()),
            "--sanctions",
            str(data_dir / "sanctions" / "maritime.csv"),
        ],
    )
    if code != 0:
        warn(f"build_live {region} exited {code}")


def _run_gfw(name: str, module: ModuleType, out: Path, args: list[str], *, offline_ok: bool) -> None:
    """Run one GFW builder, keeping a previous file when the token is the only blocker."""
    code = _call(module.main, [*args, "--out", str(out)])
    if code == 0:
        return
    if offline_ok and out.is_file():
        warn(f"{name} exited {code}; kept existing {out}")
        return
    warn(f"{name} exited {code} and wrote no {out.name}")


def _dataset(path: Path) -> dict[str, Any]:
    """Describe one snapshot file for the manifest."""
    payload = _read_payload(path)
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        for key in ("totals", "summary"):
            value = payload.get(key)
            if isinstance(value, dict):
                counts = value
                break
    if not isinstance(counts, dict):
        counts = {}

    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        for block, key in (("period", "end"), ("window", "end")):
            value = payload.get(block)
            value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, str):
                generated_at = value
                break
    if not isinstance(generated_at, str):
        generated_at = (
            datetime.fromtimestamp(path.stat().st_mtime, UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "generated_at": generated_at,
        "counts": counts,
    }


def _summary(counts: dict[str, Any]) -> str:
    parts = [f"{key}={value}" for key, value in counts.items() if isinstance(value, (int, float, str))]
    return " ".join(parts)


def _print_table(entries: list[dict[str, Any]]) -> None:
    print(f"{'file':28} {'bytes':>10}  {'generated_at':20}  counts")
    for entry in entries:
        print(
            f"{entry['file']:28} {entry['bytes']:>10}  {entry['generated_at']:20}  "
            f"{_summary(entry['counts'])}"
        )


def _missing(out_dir: Path) -> list[str]:
    missing = [name for name in REQUIRED if not _present(out_dir / name)]
    live = list(out_dir.glob("live_*.json"))
    if not any(_present(path) for path in live):
        missing.append("live_*.json")
    return missing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA, help="folder holding data/")
    parser.add_argument(
        "--regions",
        default=DEFAULT_REGIONS,
        help="comma-separated AIS region folders under data/cache/ais",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="Hormuz window start")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Hormuz window end")
    parser.add_argument(
        "--offline-ok",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep an existing GFW output when the build cannot run offline",
    )
    args = parser.parse_args(argv)

    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be given together")

    data_dir = Path(args.data_dir)
    out_dir = data_dir / "snapshot"
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = _window(data_dir, args.start, args.end)

    _run_snapshot(data_dir, out_dir)

    regions = [part.strip() for part in str(args.regions).split(",") if part.strip()]
    for region in regions:
        _run_live(region, data_dir, out_dir)

    cache = str(data_dir / "cache" / "gfw")
    _run_gfw(
        "build_unseen_fleet",
        build_unseen_fleet,
        out_dir / "hormuz_unseen.json",
        ["--start", start.isoformat(), "--end", end.isoformat(), "--cache-dir", cache],
        offline_ok=args.offline_ok,
    )
    _run_gfw(
        "build_hormuz_history",
        build_hormuz_history,
        out_dir / "hormuz_history.json",
        ["--start", start.isoformat(), "--end", end.isoformat(), "--cache-dir", cache],
        offline_ok=args.offline_ok,
    )

    entries = [_dataset(path) for path in _snapshot_files(out_dir)]
    manifest = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "datasets": entries,
    }
    _write_json(out_dir / MANIFEST, manifest)
    _print_table(entries)

    missing = _missing(out_dir)
    if missing:
        warn(f"missing required dataset(s): {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
