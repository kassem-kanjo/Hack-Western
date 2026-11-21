# API schema (frozen v1, Sat 21 Nov ~1 AM, issue #29)

Read-only JSON. Times are ISO 8601 UTC with `Z`. Coordinates are `[lon, lat]` (GeoJSON order) everywhere.
Changing a field after this point needs a PR that updates this file, the fixture and both sides.
`SNAPSHOT=1` serves the same shapes from `data/snapshot/*.json`.

## GET /api/cases?limit=50&status=candidate|ruled_out|all
```json
{ "generated_at": "2026-11-21T06:00:00Z",
  "totals": { "gaps_scanned": 5624, "with_candidates": 109, "ruled_out": 0, "meetings": 0 },
  "cases": [ CaseSummary, ... ] }
```
`CaseSummary`:
```json
{ "id": "gap:<gfw event id>", "vessel": { "name": "CL YICHUN", "mmsi": "538...", "flag": "MHL", "type": "cargo" },
  "off": { "t": "...Z", "pos": [-51.67, 7.99] }, "on": { "t": "...Z", "pos": [-48.28, 5.61] },
  "hours": 22.9, "score": 41, "band": "medium", "status": "candidate",
  "n_candidates": 2, "n_meetings": 0, "region_label": "Off French Guiana" }
```
`status`: `candidate` (≥ 1 radar candidate or meeting survives all checks), `ruled_out` (had one, failed a
check; keep it, the UI shows why), `plain` (no evidence either way).

## GET /api/cases/{id}
`CaseSummary` plus:
```json
{ "speed_cap_kn": 16.1, "speed_basis": "cargo service speed 14 kn x 1.15 (docs/research.md)",
  "straight_line_kn": 10.8,
  "footprint": GeoJSON Polygon,
  "lens_frames": [ { "t": "...Z", "frac": 0.0, "polygon": GeoJSON Polygon }, ... ],   // 24 frames
  "candidates": [ { "pos": [-50.83, 4.03], "cell_deg": 0.01, "day": "2026-09-12",
                    "scene_time": "2026-09-12T09:21:00Z" | null, "scene_source": "copernicus-stac" | null,
                    "lens_at_scene": GeoJSON Polygon | null, "inside": true, "slack": 0.42,
                    "verdict": "candidate" | "ruled_out", "reason": "outside lens at scene time" } ],
  "meetings": [ { "other_id": "gap:...", "other_vessel": {...}, "start": "...Z", "end": "...Z",
                  "max_overlap_km2": 120.5, "centroid": [lon, lat], "slack": 0.35 } ],
  "score_factors": [ { "key": "gap_hours", "label": "Gap length", "value": "22.9 h", "points": 12,
                       "note": "median gap in data: 14 h" } ],
  "sanctions": { "match": false, "source": "OpenSanctions maritime (CC BY-NC)", "entry": null },
  "sources": [ { "name": "Global Fishing Watch gap event", "dataset": "public-global-gaps-events:v5.0",
                 "accessed": "2026-11-21" } ],
  "caveats": [ "Speed cap is a class default, not measured.", "Radar misses vessels under ~25 m." ] }
```

## GET /api/stats
```json
{ "dark_hours_daily": [ { "day": "2026-09-01", "hours": 1234.5, "gaps": 61 } ],
  "validation": { "null_ratio": 3.1, "null_ci": [2.4, 3.9], "recall": 0.62, "n_recall": 120 } }
```
`validation` is filled by #27; until then `null`.

## GET /api/live/silences (optional, Baltic)  ·  GET /api/edge/status (optional, QNX)
Shapes defined in their issues (#19, #14) if those survive the cut.
