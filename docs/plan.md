# Plan v2 (evidence-based, Fri 20 Nov, midnight)

Replaces v1. Built on tonight's data probes (docs/feasibility-dark-fleet.md, docs/region-scan.md).
GitHub issues are the source of truth; this is the map.

## What changed from v1 and why
| v1 assumption | Evidence | v2 decision |
|---|---|---|
| One region (Baltic) | 0 GFW gaps in Baltic or Hormuz (> 50 nm rule); almost no Baltic SAR | **Global investigation**: the engine finds cases wherever gaps and radar overlap |
| GFW gives us gaps everywhere | Per-vessel presence API down | **Our own recording** of Baltic AIS since Fri 23:18 → our own gap detection |
| Prism + SAR would find matches | 109 of 5,624 gaps have radar candidates in their prism | Core idea confirmed; best transit case CL YICHUN off Guyana |

## The product: one engine, three views
1. **Investigate (historical, global).** GFW gap events → prisms → unmatched radar inside the prism →
   possible hidden meetings (two gaps' prisms overlapping in time) → explainable 0-100 score → case list
   and case file. Demo opener.
2. **Watch (live, Baltic).** Our own AIS recording → silences detected by us (coverage-aware) → prisms
   and meetings on our own data; sanctioned-tanker watchlist (OpenSanctions, CC BY-NC).
3. **Edge (patrol unit, QNX if a Pi appears).** Camera sees a ship that isn't broadcasting → alert offline;
   AI process killable live while the safety core keeps running.

## Who does what (DeepSeek-first)
Code is written by OpenCode DeepSeek flash coders; Builder leads run on Haiku and split work into named
coder pieces; Sonnet only for an algorithm a coder failed on; Opus Auditor once per checkpoint; Chief
merges after CI is green and the gate passes. Every piece = issue → branch → PR (`Closes #N`) → CI → merge.

## M1 End to end → CP2, Sat 3 PM
| Issue | Work | Depends |
|---|---|---|
| #25 Case builder | `scripts/build_cases.py`: gaps (global scan cache + DB) → prism footprint, radar candidates, **pairwise hidden meetings** (prefilter: time overlap + footprint intersect), filters for gear / stationary / tag-like MMSI blocks → `cases` table + JSON | core, data |
| #12 Scoring + sanctions | explainable score (factors with points, data source, caveats); OpenSanctions vessel join by IMO/MMSI | #25 |
| #19 Own gaps (Baltic) | silences from recorded AIS, coverage-aware (never a gap across a recorder outage) | recorder |
| #10 API | FastAPI read-only: `/api/cases`, `/api/cases/{id}` (gap, footprint GeoJSON, lens polygons at 24 time steps, candidates, meetings, score factors, sources), `/api/stats`, `/api/live/silences`, `/api/edge/status`; `SNAPSHOT=1` serves frozen JSON with zero network | #25 |
| #11 Console | screens 01 overview, 02 case file, 03 meeting, built one at a time and compared to the wireframe PNGs; fonts bundled; "Powered by Global Fishing Watch" on every map | #10 (mock JSON first) |

Parallel lanes Saturday morning: lane A #25 → #12 → #10 · lane B #11 against mock JSON · lane C #19.

## M2 Demo ready → CP3, Sat 10 PM
#13 live layer · #14 edge sync + console view · #7 edge on Pi (if obtained) · #15 deploy (Vultr + domain;
recorder moves to the VM) · #16 demo script rehearsed to 3:00 · #17 Entire (sessions, graph, Trails).

## M3 Submit → Sun 8:30 AM
Freeze 6 AM · Devpost + 60 s video (1280x720, 1.5x scale, big captions) · prize tracks identical on Devpost
and live site · submit by 8:30. After: DEV post from docs/insights.md.

## Cut order if behind at CP2 (keep the core)
1. Watchlist sanctions in live view → 2. live layer → 3. edge on laptop (keep only if Pi) →
4. meeting view polish. Never cut: case list + case file with prism scrubber + radar candidates + snapshot mode.

## Gates (every PR)
CI green · `make check` · tests for new behaviour · UI: screenshot vs wireframe · Chief re-runs checks.
Every checkpoint: Opus Auditor + `/code-review high`; `/security-review` before deploy.
Health loop every 30 min (`scripts/health.sh`: recorder, CI, PRs, gate on clean origin/main).
