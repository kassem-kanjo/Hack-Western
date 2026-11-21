#!/usr/bin/env python3
"""Write the edge unit's AIS snapshot: ships near the patrol position, from watch.json.

The safety core reads a JSON list of ``{mmsi, name, lat, lon, t}`` with ``t`` in Unix epoch
seconds. ``data/snapshot/watch.json`` stores ships with an ISO timestamp and far more fields,
so this script filters to the radius and converts the time. Copy the result to the Pi with
``scripts/edge_deploy.sh --ais FILE``.

Usage::

    ~/.local/bin/uv run python scripts/make_patrol_ais.py --lat 26.57 --lon 56.25 --radius-km 2 \\
        --out data/edge/patrol_ais.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Running this file directly puts ``scripts/`` first on ``sys.path``; add the repo's src so
# ``darkfleet`` imports without an editable install.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from darkfleet.edge.safety_core import haversine_km  # noqa: E402

DEFAULT_WATCH = Path("data/snapshot/watch.json")
DEFAULT_OUT = Path("data/edge/patrol_ais.json")


def _timestamp(value: Any) -> float | None:
    """Epoch seconds from an ISO-8601 string (watch.json) or a number; None if unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def select_ships(watch: Any, lat: float, lon: float, radius_km: float) -> list[dict[str, Any]]:
    """Filter ``watch["ships"]`` to those within ``radius_km`` of the patrol position."""
    if not isinstance(watch, dict) or not isinstance(watch.get("ships"), list):
        raise ValueError('watch.json must be an object with a "ships" list')
    selected: list[tuple[float, dict[str, Any]]] = []
    for ship in watch["ships"]:
        if not isinstance(ship, dict):
            continue
        try:
            ship_lat = float(ship["lat"])
            ship_lon = float(ship["lon"])
            mmsi = int(ship["mmsi"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= ship_lat <= 90.0 and -180.0 <= ship_lon <= 180.0):
            continue
        distance = haversine_km(lat, lon, ship_lat, ship_lon)
        if distance > radius_km:
            continue
        t = _timestamp(ship.get("t"))
        if t is None:
            continue
        name = ship.get("name")
        selected.append(
            (
                distance,
                {
                    "mmsi": mmsi,
                    "name": str(name) if name else "",
                    "lat": ship_lat,
                    "lon": ship_lon,
                    "t": t,
                },
            )
        )
    selected.sort(key=lambda item: item[0])
    return [record for _distance, record in selected]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIS snapshot for the edge safety core.")
    parser.add_argument("--watch", type=Path, default=DEFAULT_WATCH, help="built watch.json")
    parser.add_argument("--lat", type=float, required=True, help="patrol latitude")
    parser.add_argument("--lon", type=float, required=True, help="patrol longitude")
    parser.add_argument("--radius-km", type=float, default=2.0, help="only ships within this radius")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot JSON to write")
    args = parser.parse_args(argv)

    try:
        watch = json.loads(args.watch.read_text(encoding="utf-8"))
        ships = select_ships(watch, args.lat, args.lon, args.radius_km)
    except (OSError, ValueError) as exc:
        print(f"make_patrol_ais: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ships, indent=2) + "\n", encoding="utf-8")
    print(
        f"make_patrol_ais: {len(ships)} ship(s) within {args.radius_km:g} km -> {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
