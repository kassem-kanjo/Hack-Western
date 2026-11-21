"""Build the Hormuz story snapshot: listed ships that went dark, and outage clusters (#219).

    uv run python scripts/build_hormuz_stories.py --start 2026-01-01 --end 2026-11-21

Fetches the AIS gaps (ships that switched their transponder off) inside the Gulf box, 48-62 E and
20-30 N, from Global Fishing Watch. It keeps the gaps that touch the box and matches each ship to
the OpenSanctions maritime list by MMSI or IMO. Listed ships get a reach estimate: the speed cap
from the core `effective_cap_kn` with the GFW-implied speed as the sustained speed. Gaps that start
together, off the same spot, are grouped into outage clusters by `core.hormuz_stories`; those are
reception or jamming, not ranked as hiding.

The GFW token is read from `.env` (`GFW_API_TOKEN`) and only sent in the Authorization header.
Responses are cached under `data/cache/gfw`, so a re-run replays from disk.
Writes `data/snapshot/hormuz_stories.json`, read by `GET /api/hormuz/stories`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from darkfleet.core.geo import distance_km
from darkfleet.core.hormuz_stories import Gap, cluster_gaps, cluster_summary, reach_radius_km
from darkfleet.core.speeds import max_speed_knots
from darkfleet.core.tracks import Ping
from darkfleet.core.watch import effective_cap_kn
from darkfleet.ingest.gfw import GapEvent, GfwClient
from darkfleet.ingest.regions import polygon_from_bbox
from darkfleet.ingest.sanctions import load_vessels

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "snapshot" / "hormuz_stories.json"
DEFAULT_SANCTIONS = ROOT / "data" / "sanctions" / "maritime.csv"
BOX = (48.0, 62.0, 20.0, 30.0)  # lon_min, lon_max, lat_min, lat_max
# Reference point for "closer to the strait": the Strait of Hormuz, near Musandam.
STRAIT = (26.4, 56.3)
MAX_GAP_HOURS = 30 * 24.0
SOURCE = "Powered by Global Fishing Watch (public-global-gaps-events:latest)"
CAVEATS = [
    "AIS gaps show a transponder went silent; they do not show why",
    "Listed ships are matched on MMSI only, from the OpenSanctions maritime list (CC BY-NC 4.0)",
    "Outage clusters are reception or jamming candidates and are not ranked as hiding",
]


def in_box(lat: float, lon: float) -> bool:
    lon_min, lon_max, lat_min, lat_max = BOX
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _listed_index(vessels: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {v["mmsi"]: v for v in vessels if v.get("mmsi")}


def _cap_kn(event: GapEvent) -> float:
    """Class cap, raised to 1.2 x the speed the gap itself implies (core effective_cap_kn)."""
    class_cap = max_speed_knots(event.vessel_type)
    implied = event.implied_speed_knots
    if implied is None or implied <= 0:
        return class_cap
    track = [
        Ping(mmsi=0, t=event.start, lat=event.off_lat, lon=event.off_lon, sog=implied),
        Ping(mmsi=0, t=event.end, lat=event.on_lat, lon=event.on_lon, sog=implied),
    ]
    return effective_cap_kn(class_cap, track)


def to_gap(event: GapEvent) -> Gap:
    return Gap(
        id=event.id,
        vessel_key=event.mmsi or event.vessel_id,
        start=event.start,
        end=event.end,
        off_lat=event.off_lat,
        off_lon=event.off_lon,
        on_lat=event.on_lat,
        on_lon=event.on_lon,
        implied_kn=event.implied_speed_knots,
        class_cap_kn=max_speed_knots(event.vessel_type),
    )


def build_payload(
    events: list[GapEvent],
    vessels: list[dict[str, Any]],
    *,
    window: tuple[date, date],
    generated_at: datetime,
) -> dict[str, Any]:
    """Pure: the snapshot payload from fetched gaps and the parsed list. No I/O."""
    # A silence longer than a month is a licence, a sold hull or a dead receiver, not a dark
    # passage: it would give a reach circle covering the planet, so it is left out.
    box_events = [
        e
        for e in events
        if e.duration_hours <= MAX_GAP_HOURS and (in_box(e.off_lat, e.off_lon) or in_box(e.on_lat, e.on_lon))
    ]
    listed = _listed_index(vessels)
    listed_rows: list[dict[str, Any]] = []
    for event in sorted(box_events, key=lambda e: e.start):
        match = listed.get(event.mmsi or "")
        if match is None:
            continue
        cap = _cap_kn(event)
        gap = to_gap(event)
        listed_rows.append(
            {
                "mmsi": event.mmsi,
                "name": match.get("caption") or event.vessel_name,
                "flag": event.flag,
                "lists": match.get("risk", []),
                "start": _iso(event.start),
                "end": _iso(event.end),
                "hours": round(event.duration_hours, 1),
                "off": {"lat": event.off_lat, "lon": event.off_lon},
                "on": {"lat": event.on_lat, "lon": event.on_lon},
                "moved_km": round(event.distance_km, 1) if event.distance_km is not None else None,
                "implied_kn": round(event.implied_speed_knots, 1)
                if event.implied_speed_knots is not None
                else None,
                "cap_kn": round(cap, 1),
                "reach_km": round(reach_radius_km(cap, gap), 1),
                "closer_to_strait_km": round(
                    distance_km(event.off_lat, event.off_lon, *STRAIT)
                    - distance_km(event.on_lat, event.on_lon, *STRAIT),
                    1,
                ),
            }
        )

    by_id = {e.id: e for e in box_events}
    clusters: list[dict[str, Any]] = []
    # Grouped by where the ships came back (the issue's "same spot"). Grouping by where they went
    # dark finds none in this data; that count is kept in counts.clusters_by_departure.
    gaps = [to_gap(e) for e in box_events]
    for members in cluster_gaps(gaps, anchor="on"):
        summary = cluster_summary(members)
        first = members[0].start
        clusters.append(
            {
                "day": first.astimezone(UTC).date().isoformat(),
                "start": _iso(first),
                **summary,
                "points": [
                    {
                        "off": {"lat": by_id[g.id].off_lat, "lon": by_id[g.id].off_lon},
                        "on": {"lat": by_id[g.id].on_lat, "lon": by_id[g.id].on_lon},
                    }
                    for g in members
                ],
                "centre": {
                    "lat": round(sum(g.on_lat for g in members) / len(members), 4),
                    "lon": round(sum(g.on_lon for g in members) / len(members), 4),
                },
            }
        )
    clusters_by_departure = len(cluster_gaps(gaps, anchor="off"))

    return {
        "generated_at": _iso(generated_at),
        "source": SOURCE,
        "box": {"lon": [BOX[0], BOX[1]], "lat": [BOX[2], BOX[3]]},
        "window": {"start": window[0].isoformat(), "end": window[1].isoformat()},
        "listed": listed_rows,
        "clusters": clusters,
        "counts": {
            "gaps_in_box": len(box_events),
            "listed": len(listed_rows),
            "clusters": len(clusters),
            "cluster_vessels": sum(c["vessels"] for c in clusters),
            "clusters_by_departure": clusters_by_departure,
        },
        "caveats": CAVEATS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write the Hormuz story snapshot.")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="default: today (UTC)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sanctions", type=Path, default=DEFAULT_SANCTIONS)
    parser.add_argument("--cache-dir", default="data/cache/gfw")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(ROOT / ".env")
    token = os.environ.get("GFW_API_TOKEN", "")
    if not token:
        print("GFW_API_TOKEN is not set (copy .env.example to .env and fill it in)", file=sys.stderr)
        return 2
    end = args.end or datetime.now(UTC).date()
    client = GfwClient(token, cache_dir=args.cache_dir)
    region = polygon_from_bbox(*BOX)
    events = client.gaps(region, args.start, end)
    vessels = load_vessels(args.sanctions)
    payload = build_payload(events, vessels, window=(args.start, end), generated_at=datetime.now(UTC))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    counts = payload["counts"]
    print(
        f"wrote {args.out}: {counts['gaps_in_box']} gaps in box, {counts['listed']} listed, "
        f"{counts['clusters']} clusters ({counts['cluster_vessels']} ships)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
