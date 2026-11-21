# Watch screen contract (UI V3, approved by Kassem Sat 21 Nov ~14:15)

Kassem's words: "global in theory and we have our recording for those areas"; "suspicious things are highlighted. its cool to see all the ships"; click an alert → zoom to last seen → where it could have gone → which ships it could have met, sanctioned first. No paragraphs in the UI: names, times, distances, short labels (≤ 6 words).

## GET /api/watch  (file: data/snapshot/watch.json, rebuilt every 5 min by `scripts/build_watch.py --loop 300`)
```json
{
  "generated_at": "2026-11-21T19:20:00Z",
  "refresh_s": 300,
  "areas": [
    {"id": "BALTIC_WATCH", "name": "Baltic", "kind": "listening", "bbox": [lon_min, lat_min, lon_max, lat_max]},
    {"id": "HORMUZ_STRAIT", "name": "Strait of Hormuz", "kind": "watched", "bbox": [...]}
  ],
  "ships": [
    {"mmsi": 273128130, "name": "GESTIA", "lat": 54.87, "lon": 10.84, "t": "...Z", "sog": 0.0,
     "class": "tanker", "watchlist": "sanctioned" | "shadow_fleet" | null, "alert_ids": ["dark-273128130-..."]}
  ],
  "alerts": [
    {"id": "dark-<mmsi>-<epoch>", "kind": "dark" | "spoof" | "jump" | "meeting" | "sanctioned_stop",
     "status": "ongoing" | "ended", "mmsi": 0, "name": "AFRA STAR" | null,
     "area_id": "HORMUZ_STRAIT" | null, "severity": 0.0-1.0, "label": "Went dark",
     "t_start": "...Z", "t_end": "...Z" | null,
     "last": {"lat": 0, "lon": 0, "t": "...Z", "sog": 12.1, "cog": 90.0},
     "next": {"lat": 0, "lon": 0, "t": "...Z"} | null,
     "speed_cap_kn": 17.25,
     "contacts": [
       {"mmsi": 0, "name": "GESTIA" | null, "watchlist": "sanctioned" | null,
        "lat": 0, "lon": 0, "t": "...Z", "min_dist_km": 3.2, "stopped": true, "could_meet": true}
     ],
     "evidence": {}  /* kind-specific numbers, e.g. spoof: ghost point + per-ship offsets */
    }
  ],
  "counts": {"ships": 0, "alerts": 0, "hidden_routine": 0}
}
```
Rules:
- `alerts` contains only suspicious items: kind dark/meeting/sanctioned_stop inside a watched area, any spoof, any alert involving a watchlisted ship. Everything else is counted in `hidden_routine`, not listed.
- `ships` = latest position of every ship heard in the last 2 h across all listening areas (all shown, dimmed).
- Reach at time t for an alert: radius_km = speed_cap_kn × 1.852 × hours(t − last.t); if `next` exists, also within speed_cap_kn × 1.852 × hours(next.t − t) of `next` (the lens). Ongoing alerts: the UI grows the circle from `last.t` to now.
- `could_meet` = the contact was stopped (sog ≤ 3 kn) at a position inside the reach area at that time.

## GET /api/watch/stream  (SSE)
`event: update` with `{"generated_at": "..."}` whenever watch.json changes (mtime poll 5 s).
