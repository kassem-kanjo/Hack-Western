# API schema (frozen v1, Fri 20 Nov ~23:35 ET, issue #29)

Read-only JSON. Times are ISO 8601 UTC with `Z`. Coordinates are `[lon, lat]` (GeoJSON order) everywhere.
Changing a field after this point needs a PR that updates this file, the fixture and both sides.
`SNAPSHOT=1` serves the same shapes from `data/snapshot/*.json`.

## GET /api/cases?limit=50&status=candidate|ruled_out|all
```json
{ "generated_at": "2026-11-21T06:00:00Z",
  "totals": { "gaps_scanned": 5624, "with_candidates": 109, "ruled_out": 90, "candidates_final": 19,
              "meetings": 0,
              "funnel": [ { "stage": "scanned", "label": "Gaps scanned", "count": 5624 },
                          { "stage": "radar", "label": "Radar inside prism", "count": 109 },
                          { "stage": "unexplained", "label": "Unexplained after physics checks", "count": 19 } ] },
  "cases": [ CaseSummary, ... ] }
```
`totals.candidates_final`: gaps whose status is `candidate` after the physics checks (19); `with_candidates` stays
the 109 radar-backed baseline before the filters. `totals.funnel` is the ordered three-step funnel the UI prints
above the case list, each entry carrying a stable `stage`, a display `label` and a computed `count`.
`CaseSummary`:
```json
{ "id": "gap:<gfw event id>", "vessel": { "name": "CL YICHUN", "mmsi": "538...", "flag": "MHL", "type": "cargo" },
  "off": { "t": "...Z", "pos": [-51.67, 7.99] }, "on": { "t": "...Z", "pos": [-48.28, 5.61] },
  "hours": 22.9, "score": 41, "band": "medium", "status": "candidate",
  "n_candidates": 2, "n_meetings": 0, "region_label": "Off French Guiana",
  "score_note": "Ranks cases for analyst attention. It is not evidence of wrongdoing." }
```
`status`: `candidate` (≥ 1 radar candidate or meeting survives all checks), `ruled_out` (had one, failed a
check; keep it, the UI shows why), `plain` (no evidence either way).

## GET /api/cases/{id}
`CaseSummary` plus:
```json
{ "speed_cap_kn": 16.1, "speed_basis": "cargo service speed 14 kn x 1.15 (docs/research.md)",
  "straight_line_kn": 10.8,
  "footprint": GeoJSON Polygon | MultiPolygon,
  "lens_frames": [ { "t": "...Z", "frac": 0.0, "polygon": GeoJSON Polygon | MultiPolygon,
                     "likely": GeoJSON Polygon | MultiPolygon | null }, ... ],   // 24 frames
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
`lens_frames[].likely`: the k=2 (≈95%) Brownian-bridge contour at that frame, clipped to the space-time lens; `null`
when the contour cannot be drawn (frame outside the gap or bad geometry). It is the "most likely" shade under the
lens, not a measured track.

`footprint`, `lens_frames[].polygon` and `lens_frames[].likely` are clipped to water against a coarse 50 m
coastline, so any of them may be a `MultiPolygon` when a zone crosses a headland or island (and `null` when the
whole zone falls on land). Cases built with clipping also carry the matching caveat.

## GET /api/stats
```json
{ "dark_hours_daily": [ { "day": "2026-09-01", "hours": 1234.5, "gaps": 61 } ],
  "validation": { "null_ratio": 1.07, "null_ci": [0.91, 1.25], "null_p": 0.235,
                  "bridge_auc": 0.87, "bridge_auc_ci": [0.844, 0.896],
                  "bridge_radar_ratio": 1.16, "holdout_ratio": 0.70, "holdout_ci": [0.15, 1.37],
                  "verdict": "Radar near dark ships is not better than chance; the bridge locates ships (AUC 0.87).",
                  "source": "docs/validation.md" } }
```
`validation` is built from `data/validation/{results,bridge_results,holdout_results}.json`, falls back to `null`
per field if a file is missing, and is `null` as a whole in the API fallback.
- `null_ratio` / `null_ci` / `null_p`: primary null-test variant (real radar-in-prism count vs day-shifted count, 95% CI, permutation p).
- `bridge_auc` / `bridge_auc_ci`: paired AUC of true cells over displaced cells for the bridge score, and its 95% bootstrap CI.
- `bridge_radar_ratio`: real-vs-shuffled ratio for cells above the bridge's 95th-percentile threshold (`c_exceed`).
- `holdout_ratio` / `holdout_ci`: primary held-out (outside Brazil) null-test ratio and 95% CI.
- `verdict` / `source`: one-line plain-English reading and the document it restates (`docs/validation.md`).

## GET /api/live/silences (optional, Baltic)  ·  GET /api/edge/status (optional, QNX)
Shapes defined in their issues (#19, #14) if those survive the cut.
