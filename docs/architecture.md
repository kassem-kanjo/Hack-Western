# Architecture

Five parts, each replaceable behind a small interface. Data and maths know nothing about pixels.

```
 GFW API ─┐                         ┌─> api/ (FastAPI) ──> web/ (Vite + TS + MapLibre + deck.gl)
 aisstream ┼─> ingest/ ─> store/ ───┤        ▲
 OpenSanctions┘   (raw cache)        └─> core/ (pure Python: prisms, matching, rendezvous, scoring)
                                              ▲
 edge/ (laptop or QNX Pi): camera -> detector (untrusted) -> safety core -> alert log -> sync to api/
```

## Components
| Dir | Language | Job | Key libs |
|---|---|---|---|
| `ingest/` | Python 3.12 | Fetch + cache raw JSON (GFW events: gaps, encounters, loitering, port visits; vessel identity; 4Wings SAR), aisstream WebSocket collector, OpenSanctions vessel list | httpx, websockets |
| `core/` | Python 3.12 | **Pure functions, no I/O.** Space-time prism (lens) geometry on the sphere; SAR-in-prism matching; dark rendezvous search; explainable risk score | numpy, shapely, pyproj; Hypothesis tests |
| `store/` | SQL + Python | Repository interface; Postgres/Timescale (Tiger Data) hypertables `positions`, `gap_events`, `sar_detections`, `cases`; continuous aggregate `dark_hours_daily`. Fallback: SQLite | psycopg 3 |
| `api/` | Python | Read-only JSON: `/regions`, `/cases`, `/cases/{id}`, `/vessels/{id}`, `/stats/daily`, `/live` (SSE). Snapshot mode serves a frozen bundle with zero network | FastAPI, uvicorn |
| `web/` | TypeScript | Operations console: map, timeline, case file, live layer. Design tokens in one file | Vite, MapLibre GL, deck.gl |
| `edge/` | Python | Two processes + supervisor (below); same code on macOS and QNX | onnxruntime, opencv |

## The core algorithm (space-time prism)
Inputs: last fix before the gap `(A, t0)`, first fix after `(B, t1)`, a max speed `v` (vessel-class
default, e.g. tanker 15 kn, plus margin). At time `t` in `[t0, t1]` the ship lies within
`disk(A, v·(t−t0)) ∩ disk(B, v·(t1−t))` (geodesic disks). The whole-gap footprint is the union over `t`
(an ellipse with foci A and B and major axis `v·(t1−t0)`). If `dist(A,B) > v·(t1−t0)` the data is
inconsistent: flag as possible spoofing/identity swap rather than a gap.
- **SAR match:** a radar detection at `(p, t)` is a candidate sighting if `p` is inside the time-`t` lens
  and no broadcasting vessel was within the match radius at `t`.
- **Dark rendezvous:** ships X and Y can have met at time `t` if their time-`t` lenses intersect, for
  some `t` in both gaps; we sample `t` on a grid and refine. Score by overlap duration and lens size.
- **Proof obligations (property tests):** A in lens at t0, B in lens at t1; lens shrinks to a point when
  `v·(t1−t0) == dist(A,B)`; lens is empty iff inconsistent; monotonic in `v`; symmetric under time reversal.

## Edge unit (mission-critical pattern)
- `safety_core` (high priority, tiny, no ML): owns the AIS cache, the alert state machine and the
  deadline clock (e.g. 200 ms per frame). Never blocks on the detector.
- `detector` (low priority, untrusted): camera → ONNX "boat" detections → IPC to safety core.
- `supervisor`: watchdog; restarts a dead or late detector, logs recovery time; safety core reports
  `DEGRADED` while the detector is down.
- IPC: local socket with length-prefixed JSON; portable to QNX (POSIX). Priorities via
  `os.sched_setscheduler` where supported, `nice` fallback on macOS.
- Alerts stored locally, synced to `api/` when a link exists.

## Demo reliability
`make snapshot` freezes all API responses for the demo region/time into `data/snapshot/`; the API serves
it with `SNAPSHOT=1`. The console never calls third parties directly (keys stay server-side).

## Decisions (ADRs, short form)
- **ADR-1 Python for data/geo/edge, TypeScript for UI.** Geo ecosystem and onnxruntime on QNX are
  Python-first; GPU map layers are TS-first.
- **ADR-2 One region done well** over global coverage (time, rate limits, demo clarity).
- **ADR-3 Prisms over ML for the core.** Explainable, provable, fast; ML only at the edge where it is
  required (vision).
- **ADR-4 Storage behind an interface.** Tiger Data if available; never blocks the build.
- **ADR-5 Snapshot mode is a first-class feature**, not a fallback hack.
