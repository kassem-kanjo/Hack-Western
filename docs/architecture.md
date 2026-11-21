# Architecture

One physics engine, three theatres: the global GFW investigation, our own live AIS recording, and
the edge unit. Data and maths never import the web app; the API is read-only and file-backed.

## Data flow

```
 INPUTS                              ENGINE                              OUTPUT
 aisstream.io ─record_ais.py─▶ data/cache/ais/<REGION>/*.jsonl.gz
                                      │ ingest/ais_tracks.py
                                      ▼
                               scripts/build_live.py ──────────────▶ live_<REGION>.json
                               core/kinematics.py (silence/jump/clone)
                               core/encounters.py (meeting/dark meeting)
                               core/spoofing.py (ghost point)

 GFW v3 API ──scan_regions.py──▶ rerank_cases.py ──▶ cases_v2.json
 Copernicus STAC ─scene times──▶   (prism + bridge)        │
                                                           ▼
 OpenSanctions ─watchlist──▶ build_snapshot.py ──────────▶ cases.json, stats.json
        └────────────────────▶ build_live.py (watchlist block, above)
 GFW ──build_unseen_fleet.py─────────────────────────────▶ hormuz_unseen.json
 GFW ──build_hormuz_history.py────────────────────────────▶ hormuz_history.json

 scripts/build_all.py ──▶ data/snapshot/*.json  (atomic .tmp + os.replace)
                                     │ optional --to-db
                                     ▼
              store/: Timescale hypertables + continuous aggregates
                                     │
       api/ (FastAPI, read-only, file-backed) ──HTTP/SSE──▶ web/ console

 edge/ unit: camera ─▶ detector.py (untrusted) ─IPC─▶ safety_core.py (trusted) ─▶ alerts.jsonl
```

## Modules

| Area | File(s) | One-line job |
|---|---|---|
| Contract | `src/darkfleet/core/tracks.py` | `Ping`, `VesselInfo`, `CoverageMinute`, `Event`; `ship_class` maps AIS 40–49 to hsc, 50–59 to service, 80–89 tanker, 70–79 cargo |
| Kinematics | `src/darkfleet/core/kinematics.py` | Clean tracks, per-minute coverage, silences, impossible jumps, clone episodes |
| Encounters | `src/darkfleet/core/encounters.py` | Visible slow-and-close meetings, twin-transponder runs, dark meetings between prisms |
| Spoofing | `src/darkfleet/core/spoofing.py` | Ghost points where unrelated ships teleported out and back |
| Prism | `src/darkfleet/core/prism.py` | Geodesic space-time lens, day lens, whole-gap footprint |
| Bridge | `src/darkfleet/core/bridge.py` | Brownian-bridge mean, variance, k-sigma contour, sigma fit, paired AUC |
| Match | `src/darkfleet/core/match.py` | SAR cell inside its day lens; rendezvous windows between two prisms |
| Speeds | `src/darkfleet/core/speeds.py` | Per-class speed caps with the 1.15 margin |
| Geometry | `src/darkfleet/core/geo.py` | Shared geodesic distance helpers |
| Filters | `src/darkfleet/core/filters.py` | Stationary gap, recurring radar cell, meeting-slack filters |
| Scoring | `src/darkfleet/core/scoring.py` | Transparent 0–100 analyst-attention score with per-factor notes |
| Unseen fleet | `src/darkfleet/core/unseen.py` | Daily radar-vs-AIS counts, 0.1° density grid, bootstrap summary |
| History | `src/darkfleet/core/history.py` | GFW encounter/loitering shaping and 0.1° hotspots |
| Water, anchorages | `src/darkfleet/core/water.py`, `core/anchorages.py` | Clip zones to 50 m land; anchorage and EOPL STS boxes |
| Validation | `src/darkfleet/core/validation.py`, `core/track_validation.py` | Null tests, bootstrap CIs, masked-track checks |
| AIS ingest | `src/darkfleet/ingest/ais_tracks.py` | Parse aisstream JSONL, time-merge hourly files, per-minute coverage |
| GFW ingest | `src/darkfleet/ingest/gfw.py` | Typed GFW v3 client (gaps, encounters, loitering, SAR) with disk cache |
| Other ingest | `src/darkfleet/ingest/stac.py`, `sanctions.py`, `watchlist.py`, `regions.py` | Sentinel-1 scene times; OpenSanctions index and IMO join; named region polygons |
| Store | `src/darkfleet/store/schema.sql`, `timescale_repo.py`, `sqlite_repo.py`, `factory.py` | Hypertables and continuous aggregates; SQLite fallback chosen by `DATABASE_URL` |
| API | `src/darkfleet/api/app.py`, `live.py`, `hormuz.py` | `/api/cases`, `/api/stats`; live snapshots + SSE; `/api/hormuz/*` |
| Console | `web/src/main.ts`, `screens/`, `components/`, `present/`, `map/` | Hash router, overview/case/live/patrol/Hormuz screens, presentation overlay |
| Edge | `src/darkfleet/edge/detector.py`, `safety_core.py`, `protocol.py`, `supervisor.py` | ONNX boat vision; trusted alert state machine; IPC; watchdog |
| Builders | `scripts/record_ais.py`, `build_live.py`, `build_snapshot.py`, `build_unseen_fleet.py`, `build_hormuz_history.py`, `build_all.py` | Record, run the engine, and freeze every snapshot in dependency order |
| Data loads | `scripts/load_ais.py`, `ingest_region.py`, `export_stats.py`, `scan_regions.py`, `rerank_cases.py`, `validate*.py` | Load AIS/GFW into the store, export stats, and run the pre-declared validations |

## Physics rules (thresholds as coded)

### Silence — `core/kinematics.py`
- Gap strictly longer than **60 min** (`min_gap_min=60`), and the last ping's SOG at or above
  **2 kn** (`min_sog=2.0`), never below the **0.5 kn** berth floor (`BERTH_SOG_KN`).
- Global coverage: **≥ 150 messages in every UTC minute** of the gap (`covered`,
  `min_msgs_per_min=150`); a missing minute is zero.
- Local coverage: **≥ 2 other MMSIs within 60 km in every 15-min bucket** (`local_activity`,
  `_DEFAULT_LOCAL_RADIUS_KM=60`, `_DEFAULT_ACTIVITY_BUCKET_MIN=15`, `_DEFAULT_MIN_LOCAL_VESSELS=2`),
  looked up in 0.5° cells (`ACTIVITY_CELL_DEG`).
- Severity = ½·min(1, minutes/240) + ½·min(1, km/200), halved when it reappears **< 2 km** away.

### Jump — `core/kinematics.py`
- Implied speed between consecutive pings above **1.5 × the class cap** (`factor=1.5`) over more
  than **5 km** (`min_km=5.0`).
- Caps are `max_speed_knots(class)` from `core/speeds.py`: table 14/15/12/20/42/30 kn × **1.15**.
  Cargo/bulk/other 16.1, tanker/carrier 17.25, fishing 13.8, passenger 23.0, **hsc 48.3**
  (AIS 40–49), **service 34.5** (AIS 50–59); unknown hulls get the 14 kn default (16.1).
- Severity `min(1, ratio/3)`, so it reaches 1.0 at **3× the cap**.

### Clone — `core/kinematics.py`
- Two clusters at least **30 km** apart (`min_km=30`), alternating at **> 55 kn**
  (`CLONE_IMPLIED_KN=55`) at least **3 times** (`min_flips=3`) inside a **6 h** span
  (`CLONE_WINDOW_MIN=360`).

### Spoof (ghost point) — `core/spoofing.py`
- Out-and-back only: the leg in and the leg out must both exceed **5 km** (`MIN_LEG_KM`) and
  **3 × the default 16.1 kn cap = 48.3 kn** (`SPEED_FACTOR=3.0`, `DEFAULT_CAP_KN`).
- The visit must sit at least **20 km** from the vessel's real track (`min_offset_km=20`).
- **≥ 3 distinct MMSIs** must share one **500 m** disk (`min_vessels=3`, `radius_m=500`),
  bucketed at 0.005° (`BUCKET_DEG`).
- Land never triggers an event; it is extra evidence (`min_inland_km=1.0`). Severity
  `min(1, n/6)` plus 0.2 if on land.

### Meeting — `core/encounters.py`
- Tracks resampled on a **5-min** clock (`STEP_MINUTES`); a step with no ping within **10 min**
  (`NEAREST_PING_MINUTES`) is skipped.
- Both vessels within **500 m** (`max_m=500`), both SOG **≤ 3 kn** (`max_sog=3.0`), for at least
  **30 min** (`min_minutes=30`), neither in AIS status 5 (moored).
- Offshore: `scripts/build_live.py` passes **5 km** (`--min-offshore-km`, default 5.0); core's own
  default is `LAND_CLEAR_KM=3.0`. Default ship types are AIS **70–89** (cargo + tanker).
- A pair whose separation stays under **5 m** (`TWIN_MAX_M`) is one hull broadcasting twice,
  returned by `twin_transponders` instead.
- Severity = 0.6·duration (0.3 at 30 min → 1.0 at **180 min**) + 0.4·min(1, offshore km/**20**).
  Context in `core/anchorages.py` + `build_live.py`: routine anchorage **halves** severity; EOPL
  STS hotspot floors it at **0.8**; a sanctions/shadow-fleet hull floors it at **0.9**.

### Dark meeting — `core/encounters.py`, `core/match.py`
- One silence prism per vessel, windows sampled every **15 min** (`rendezvous_windows`); only
  overlapping silence intervals are compared. Severity = ½·area/1000 km² + ½·minutes/120.

### Prism and Brownian bridge — `core/prism.py`, `core/bridge.py`
- Lens at time `t`: `d(a,p) ≤ v(t−t0)` **and** `d(p,b) ≤ v(t1−t)` on WGS84 geodesics; the union
  over `t` is the footprint. `d(a,b) > v(t1−t0)` is inconsistent, a spoof/identity swap signal.
- Circles use 128 vertices; day lenses union 30-min samples; the footprint boundary is bisected
  45 times from the midpoint.
- Bridge mean is geodesic interpolation; variance `max(σ² τ(1−τ)T, 1.0 km²)` with
  **σ = 21.62 km/√h** (`DEFAULT_SIGMA_KM_PER_SQRT_H`) and a **k = 2** (≈95%) contour, clipped to
  the lens. Cells are scored at 30-min samples, `-inf` outside the lens.

### Global case checks — `scripts/rerank_cases.py`, `core/filters.py`
- A gap whose ends are within **5 km** is stationary (`STATIONARY_KM`); radar cells recurring on
  **≥ 3 distinct days** are fixed structures; a SAR cell needs no AIS position within **2 km**
  that day (`candidate_sightings`, `clear_km=2.0`).

## Snapshot mode and atomic writes

`make snapshot` runs `scripts/build_all.py`, which calls every builder in dependency order and
writes `data/snapshot/manifest.json`: `cases.json` + `stats.json` (global), `live_<REGION>.json`
per recorded region, `hormuz_unseen.json`, `hormuz_history.json`. A required file that is missing
or empty makes the run exit 1, and a GFW builder that cannot reach the network keeps its previous
file when `--offline-ok` is on (default).

Bookkeeping files are written through a sibling `path.tmp` and moved with `os.replace`
(`_write_json` in `build_snapshot.py`, `build_live.py`, `build_all.py`), so the API never reads
half-written JSON; `build_hormuz_history.py` and `build_unseen_fleet.py` write in place.

The API reads only files under `DARKFLEET_DATA` (default `data/snapshot`), loads each file once
per directory or path+mtime (`lru_cache`), answers 404 JSON for an unknown case, region or missing
Hormuz file, and needs no database or network. `api/live.py` optionally tails the newest recorder
file when `DARKFLEET_AIS_DIR` is set: SSE, a heartbeat every 5 s and at most 200 positions per
tick. The console labels itself `SNAPSHOT` or `LIVE` (`web/src/components/TopBar.ts`).

## Security notes

- Keys live in `.env` only. `GFW_API_TOKEN` and `AISSTREAM_API_KEY` are read by scripts and sent
  server-side (Authorization header / websocket subscribe); the browser never calls a third party.
- CORS allows only `http://localhost:5173` and `http://127.0.0.1:5173`, GET only (`api/app.py`).
- A region name must match `^[A-Z_]+$` before it is joined into a path (`api/live.py`).
- The console is offline-capable: the MapLibre worker is bundled, not fetched from a CDN
  (`web/src/main.ts`), and all layers come from `/api`.
- OpenSanctions data is CC BY-NC and never committed; a missing CSV leaves `sanctions: null` and
  the watchlist explicitly "not checked" (`build_live.py`).
- The edge detector is untrusted: framed JSON is capped at 1 MiB on receive, and the safety core
  keeps running, reporting `DEGRADED`, when the detector dies or is silent for 3× its 200 ms
  deadline (0.6 s).

## Decisions (ADRs, short form)

- **ADR-1 Python for data/geo/edge, TypeScript for UI.** Geo ecosystem and onnxruntime on QNX are
  Python-first; GPU map layers are TS-first.
- **ADR-2 One region done well** over global coverage (time, rate limits, demo clarity).
- **ADR-3 Prisms over ML for the core.** Explainable, provable, fast; ML only at the edge where it
  is required (vision).
- **ADR-4 Storage behind an interface.** Tiger Data if available; never blocks the build.
- **ADR-5 Snapshot mode is a first-class feature**, not a fallback hack.
- **ADR-6 Physics before ML for accusations.** Every finding is a stated distance, speed and
  duration that a judge can recompute; the score ranks attention, it is not evidence.
- **ADR-7 Record our own AIS.** GFW publishes only gaps > 50 nm offshore, so we run our own
  recorder for full tracks, near-shore silences and coverage we control.
- **ADR-8 Hormuz leads, live on Baltic/EOPL.** Hormuz carries the story and the radar layer; the
  live engine proves it runs anywhere, with EOPL as the ship-to-ship hot spot.
