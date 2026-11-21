"""Build the Hormuz history snapshot: encounters and long loitering events near Fujairah.

    uv run python scripts/build_hormuz_history.py --start 2026-08-15 --end 2026-11-15

The output is a single read-only file, ``data/snapshot/hormuz_history.json``, that the history view
can draw without talking to GFW. Two event layers are fetched for the HORMUZ polygon: encounters
(two ships close together) and loitering, where only dwells longer than six hours are kept because
short stops are ordinary queueing and anchorage behaviour rather than a rendezvous signal. Both
layers then share one 0.1-degree density grid, so the map can show where vessels bunch up.

The GFW token is read from ``.env`` (``GFW_API_TOKEN``) and only ever sent in the Authorization
header. Responses are cached under ``data/cache/gfw`` by the client, so re-running the same window
replays from disk and makes no new API calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from darkfleet.core.history import hotspots, to_history_items
from darkfleet.ingest.regions import region_geometry

ROOT = Path(__file__).resolve().parents[1]
REGION = "HORMUZ"
DEFAULT_OUT = ROOT / "data" / "snapshot" / "hormuz_history.json"
MIN_LOITERING_HOURS = 6.0

SOURCE = (
    "Powered by Global Fishing Watch, v3 events API "
    "(public-global-encounters-events:latest, public-global-loitering-events:latest)"
)

CAVEATS = [
    "GFW events are AIS-based: ships with AIS off are not in this layer",
    "Encounters cover fishing/carrier/tanker-fishing types per GFW",
    "Non-commercial data (CC BY-NC)",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write the Strait of Hormuz history snapshot.")
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot file to write")
    parser.add_argument("--cache-dir", default="data/cache/gfw", help="raw response cache folder")
    return parser


def build(client: Any, start: date, end: date) -> dict[str, Any]:
    """Fetch both event layers and shape the snapshot payload.

    Loitering records without a usable duration are dropped: the six-hour floor cannot be proven
    for them, and inventing a duration would put an unverifiable point on the map.
    """
    geometry = region_geometry(REGION)
    encounter_items = to_history_items(client.encounters(geometry, start, end))
    raw_loitering = client.loitering(geometry, start, end)
    loitering_items = [
        item
        for item in to_history_items(raw_loitering)
        if item["hours"] is not None and item["hours"] > MIN_LOITERING_HOURS
    ]

    return {
        "region": REGION,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "encounters": encounter_items,
        "loitering": loitering_items,
        "counts": {
            "encounters": len(encounter_items),
            "loitering": len(loitering_items),
            "total": len(encounter_items) + len(loitering_items),
        },
        "hotspots": hotspots(encounter_items + loitering_items),
        "source": SOURCE,
        "caveats": list(CAVEATS),
    }


def write_snapshot(payload: dict[str, Any], out: Path) -> None:
    """Write the snapshot with the same indentation and trailing newline as the other exports."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None, *, client: Any = None) -> int:
    """Run the build. ``client`` is an injection point for tests; the CLI leaves it None."""
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

    payload = build(client, args.start, args.end)
    write_snapshot(payload, args.out)
    counts = payload["counts"]
    print(
        f"wrote {args.out}: region={REGION} period={args.start}..{args.end} "
        f"encounters={counts['encounters']} loitering={counts['loitering']} "
        f"(over {MIN_LOITERING_HOURS:g} h) hotspots={len(payload['hotspots'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
