# Watch screen contract (UI V3, approved by Kassem Sat 21 Nov ~14:15)

Kassem's words: "global in theory and we have our recording for those areas"; "suspicious things are highlighted. its cool to see all the ships"; click an alert → zoom to last seen → where it could have gone → which ships it could have met, sanctioned first. No paragraphs in the UI: names, times, distances, short labels (≤ 6 words).

## GET /api/watch  (file: data/snapshot/watch.json, rebuilt every 5 min by `scripts/build_watch.py --loop 300`)
```json
{
  "generated_at": "2026-11-21T19:20:00Z",
  "refresh_s": 300,
  "recording": {
    "latest_t": "2026-11-21T19:19:31Z",
    "lag_s": 29,
    "gaps": [
      {"region": "BALTIC_WATCH", "start": "2026-11-21T16:02:00Z", "end": "2026-11-21T16:11:00Z"}
    ]
  },
  "areas": [
    {"id": "BALTIC_WATCH", "name": "Baltic", "kind": "listening", "bbox": [lon_min, lat_min, lon_max, lat_max]},
    {"id": "HORMUZ_STRAIT", "name": "Strait of Hormuz", "kind": "watched", "bbox": [...]}
  ],
  "ships": [
    {"mmsi": 273128130, "valid": true, "name": "GESTIA", "lat": 54.87, "lon": 10.84, "t": "...Z", "sog": 0.0, "cog": 90.0 | null,
     "class": "tanker", "watchlist": "sanctioned" | "shadow_fleet" | null, "alert_ids": ["dark-273128130-..."]}
  ],
  "alerts": [
    {"id": "dark-<mmsi>-<epoch>", "kind": "dark" | "spoof" | "jump" | "meeting" | "sanctioned_stop" | "radar_unmatched",
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
     "trail": [[56.28, 26.62, "...Z"], ...],  /* up to 60 [[lon, lat, "t"]], time order */
     "evidence": {}  /* kind-specific numbers, e.g. spoof: ghost point + per-ship offsets */
    }
  ],
  "counts": {"ships": 0, "alerts": 0, "hidden_routine": 0}
}
```
Rules:
- `alerts` contains only suspicious items: kind dark/meeting/sanctioned_stop inside a watched area, any spoof, any alert involving a watchlisted ship, and the daily fleet-level `radar_unmatched` alert. Everything else is counted in `hidden_routine`, not listed.
- **Identity gate.** An MMSI that cannot be a real ship identity — not 9 digits, every digit the same, a straight run such as `123456789`, or a first-three-digit prefix outside the 201–775 Maritime Identification Digits range — never produces an alert of any kind and never appears as a contact. `ships` still lists it, marked `"valid": false`, because the map should show what the receiver heard without claiming it is a ship. (The fleet-level radar alert has `mmsi: 0` by design; it is not an identity.)
- `ships` = latest position of every ship heard in the last 2 h across all listening areas (all shown, dimmed). Each entry carries `cog`, its last reported course in degrees, or `null` when the hull sent none; the map draws a chevron only when both `cog` and `sog >= 0.5` are present.
- **Leaving coverage is not going dark.** A dark alert whose last fix is inside a listening box, within 25 km of its edge, and steering out of it (course within 60° of that edge's outward normal) is dropped for both ongoing and ended gaps: the hull drove out of our receivers. Fixes outside every box, or with no course, are never suppressed this way. `--explain` counts these as `left coverage`.
- Reach at time t for an alert: radius_km = speed_cap_kn × 1.852 × hours(t − last.t); if `next` exists, also within speed_cap_kn × 1.852 × hours(next.t − t) of `next` (the lens). Ongoing alerts: the UI grows the circle from `last.t` to now.
- `speed_cap_kn` for a dark alert is `effective_cap_kn(class cap, the ship's track in the recording window)`: the larger of the class bound and 1.2 × the fastest SOG two consecutive fixes both report, clamped to 45 kn. A hull filing a slow class but really making 30 kn therefore gets 36 kn, and its `contacts` are computed with that same cap. Other kinds keep the plain class cap.
- `could_meet` = the contact was stopped (sog ≤ 3 kn) at a position inside the reach area at that time, offshore and outside a routine anchorage.
- `recording` is the freshness block. `latest_t` is the newest AIS fix across all regions. `lag_s` is whole seconds between the build's clock (`generated_at`) and `latest_t`, clamped at 0, so the UI can say "last update N min ago" and keep counting while the recorder is stopped; it is `null` when no recording was read. `gaps` lists receiver outages longer than 3 minutes inside a region's recorded window as inclusive minute labels `{region, start, end}`: adjacent minutes in which that region heard nothing (0 messages) merge into one row and shorter quiet spells are not reported. Leading/trailing silence outside the recording is not a gap; `lag_s` is what describes that.
- `trail` is the ship's own recent path: its AIS fixes in the 6 h before `t_start`, plus, for an
  ended alert, up to 2 h of fixes after `t_end`. At most 60 points, decimated evenly with both ends
  kept, as `[[lon, lat, "t"], ...]` in time order. It is `[]` when the alert has no AIS track
  (a radar alert, or a hull whose fixes fall outside that window). Trail points and radar cells
  are written one per line rather than one number per line, so a sample-shaped snapshot (300
  ships, five trails, 200 cells) grows 25.5 %, under the 40 % budget.

### Radar alerts (`kind: "radar_unmatched"`)
One per build, from `data/snapshot/hormuz_unseen.json` (`scripts/build_unseen_fleet.py`). It is
fleet-level evidence: radar saw vessels that were not broadcasting AIS, and the snapshot never
names them. The alert covers the latest *well-covered* UTC day in the file (a day listed in
`summary.excluded_days` is a reception gap and is skipped; if every day is excluded, the newest
day is used). If the file is missing or unreadable, no radar alert is emitted.

| field | value |
| --- | --- |
| `status` | `"ended"` |
| `mmsi` / `name` | `0` / `null` (no individual ship is claimed) |
| `area_id` | `"HORMUZ_STRAIT"` |
| `severity` | `0.9` |
| `label` | `"Radar: N vessels not broadcasting"`, N = that day's unmatched detections |
| `t_start` / `t_end` | that UTC day, `00:00:00Z` to `23:59:59Z` |
| `last` | the watched box's centroid, `sog`/`cog` null; `next` null |
| `speed_cap_kn` / `contacts` / `trail` | `0.0` / `[]` / `[]` (no reach, no named contacts) |

`evidence` carries `{"day", "seen", "unmatched", "share", "median_share_well_covered_days",
"days_used", "cells", "source"}`: `unmatched / seen` for the day, the median daily share over the
well-covered days, how many days that median used, and up to 200 `[[lon, lat], ...]` cells of
unmatched detections (densest first). `source` is always `"Powered by Global Fishing Watch"`, the
attribution every radar view must show.

## GET /api/watch/stream  (SSE)
`event: update` with `{"generated_at": "..."}` whenever watch.json changes (mtime poll 5 s).

## GET /api/edge/sightings  (patrol unit, issue #220)
The Pi's hash-chained log of "vessel not on AIS" sightings, relayed by the laptop API (404 until
the unit has logged one). `{"chain": {"ok", "records", "last_seq", "last_t_utc", "torn_tail"},
"sightings": [<log records, newest first, last 20>], "feed": [...]}`. Each `feed` item is ready for
the Watch feed with `kind: "patrol_sighting"`: `id` (first 16 hex of the record hash), `t` (UTC
ISO), `lat`, `lon` (the patrol position, not the ship's), `title` "Vessel not on AIS", `detail`,
`trend` (`closing`/`opening`/`steady`/null), `frame_sha256`, `seq`. Show it as an alert-class story
at the patrol position; there is no vessel identity, MMSI or track.
