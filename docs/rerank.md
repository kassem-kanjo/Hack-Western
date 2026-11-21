# Re-rank of the 109 radar-backed gaps (issue #28)

Each of the 109 Global Fishing Watch gaps that had at least one unmatched radar (Sentinel-1) detection in its
possible-reach area was re-checked with four changes: a stationary filter, a recurring-cell filter, class service
speeds, and the real scene time of each radar detection. 19 gaps still have a radar detection their ship could
have reached at the time of the scene. 90 do not.

The numbers come from one run of `scripts/rerank_cases.py` on the cached data. The script reads only the cache
(no Global Fishing Watch calls); the one live service is the Copernicus STAC search, and all 290 unique
(cell, day) lookups were answered with no errors.

## What changed from the region scan
- **Speeds.** The scan capped ships at 19.55 kn (tanker, cargo, carrier, other), 16.1 (fishing), 28.75
  (passenger), 20.7 (default). The re-rank uses class service speeds with the same 1.15 margin:
  tanker 17.25, cargo 16.1, carrier 17.25, other 16.1, fishing 13.8, passenger 23.0, default 16.1. These are
  **assumed service speeds (no primary source found)**; docs/research.md section 5 has no sourced figure for
  most classes. Treat every reachability result as conditional on them.
- **Scene time.** The scan knew only the date of each radar detection, so it tested the cell against the lens
  for the whole day. The re-rank asks Copernicus Data Space (Sentinel-1 GRD, free, no login) for the scenes
  covering each cell on that day and tests the cell against the lens at the scene's own time.
- **Filters.** Gaps where the off and on positions are within 5 km are not cases. A radar cell that shows a
  detection on 3 or more different days is treated as a fixed structure and dropped.

## Counts before and after each step
| Step | Gaps | Radar candidates |
|---|---:|---:|
| Baseline (region scan, old speed caps) | 109 | 1,292 |
| A. Stationary filter (off and on within 5 km) | 50 left (59 removed) | 522 left (770 removed) |
| B. Recurring cells (3 or more days) | 50 | 493 left (29 removed) |
| C. Class service speeds (new caps) | 40 with at least 1 candidate (10 out of reach) | 404 left (89 removed) |
| D. Scene times found | 40 | 404 with a scene, 0 without, 0 lookup errors |
| E. Lens tested at scene time | 40 | 190 inside, 214 outside |
| **Final** | **19 candidate, 90 ruled out** | |

Ruled-out reasons (90 gaps): stationary 59; no candidate inside the lens at its scene time 16; no radar cell
reachable at the class speed cap 10; the only scene was outside the gap 5. Baseline reproduction: the scan's own
numbers (109 gaps, 1,292 candidates) were reproduced from the cache before any filter was applied.

## Top 10 surviving gaps
"Candidates" is the number of radar cells that pass the scene-time test out of all cells left after stage C.
"Slack" is the margin inside the lens, from 0 (on the edge) to 1 (deep inside); it does not measure how
specific the detection is.

| # | Vessel (MMSI) | Type | Region | Gap hours | Candidates | Slack | Scene time (UTC) |
|---:|---|---|---|---:|---:|---:|---|
| 1 | IRENES WISDOM (210738000) | other | -25..-20N, -45..-40E | 80.8 | 37 of 37 | 0.99 | 2026-10-02 08:22:04 |
| 2 | 210700028 (210700028) | insufficient data | 5..10N, -50..-45E | 83.4 | 4 of 4 | 0.85 | 2026-10-24 21:42:54 |
| 3 | ASSO VENTISEI (710001520) | other | -25..-20N, -45..-40E | 49.3 | 30 of 30 | 0.62 | 2026-08-15 08:21:38 |
| 4 | ONWA18357 (9118357) | other | -30..-25N, -45..-40E | 53.8 | 5 of 7 | 0.61 | 2026-09-08 08:22:04 |
| 5 | FU YUAN YU7869 (412440658) | fishing | -20..-15N, -80..-75E | 85.4 | 7 of 8 | 0.53 | 2026-08-29 10:31:22 |
| 6 | 210700221 (210700221) | insufficient data | 5..10N, -50..-45E | 19.7 | 1 of 1 | 0.39 | 2026-10-04 08:47:12 |
| 7 | CAP JACSK SPARROW (9129220) | fishing | -30..-25N, -45..-40E | 79.5 | 6 of 6 | 0.38 | 2026-10-02 08:22:04 |
| 8 | 210700099 (210700099) | insufficient data | 5..10N, -50..-45E | 39.6 | 1 of 1 | 0.31 | 2026-09-10 08:47:12 |
| 9 | 210700220 (210700220) | insufficient data | 5..10N, -50..-45E | 16.0 | 1 of 1 | 0.29 | 2026-10-04 08:47:12 |
| 10 | HONG RUN 16 (412420951) | fishing | -20..-15N, -80..-75E | 82.9 | 6 of 7 | 0.28 | 2026-08-23 23:32:40 |

The ranking is: survivors first, then the best slack, then gap hours. Rank 1 is a long gap with many detections
from one radar pass, so it is the weakest kind of evidence: the large lens lets many detections in one pass
fit. The fixture (`tests/fixtures/api/case.json`) is rank 1 because the brief asked for the top survivor.

## Three instructive ruled-out cases
1. **CL YICHUN (538010275), cargo, 5..10N, -50..-45E.** This was the Auditor's opener. At the old cap it had a
   radar cell at (4.03, -50.83) on 12 Sep. At the cargo cap of 16.1 kn that cell is no longer reachable in the
   gap (the Auditor's own figure was 18.4 kn needed). Separately, the Sentinel-1 scene that covers that cell on
   12 Sep is at 21:42 UTC, 4 h 46 min after the gap closed at 16:56 UTC. The two checks agree: the scene is not
   within the gap.
2. **WIN FAR FU NO 1 (416241900), fishing, 40..45N, 150..155E.** The day-level test said 19 cells could have
   been reached. All 19 are on 12 Oct and share one scene at 07:54:41 UTC. At that instant the vessel's lens
   did not reach any of the cells. Five other fishing vessels in the same cell fail the same way at that scene.
   This is the case the day-level test overstated.
3. **210700028 (210700028), an earlier gap, 5..10N, -50..-45E.** The same MMSI as survivor 2 has another gap
   (29 Sep 06:31 to 30 Sep 15:12 UTC, 32.7 h, 2.2 kn straight-line). Its one candidate's scene is at
   21:42:54 UTC on 30 Sep, 6 h 30 min after the gap closed. The later gap of the same MMSI survives. Two gaps of
   one ship give different verdicts, so the verdict depends on the scene time, not on the ship.

The old best case, EAGLE CAMPOS (533132177), is now ruled out as stationary: its off and on positions are 1.2 km
apart. The same holds for EAGLE CANOAS and LISBOA.

## Caveats
- Every number depends on the assumed service speeds above. A ship that really is faster than the class cap
  would be missed.
- Scene times come from the scenes whose footprint contains the cell centre. A cell at the very edge of a scene
  may still be unverified in reality; the check is the scene's footprint, not the radar's own coverage.
- Radar misses vessels under about 25 m. A detection in an offshore field may be a platform or a buoy even when it
  does not recur on 3 days.
- Fishing fleets in the 40..45N cell are numerous and cluster in time; a detection can match a ship that is not
  the dark one.
- This is not a finding about any named ship. The survivors are candidates for a closer look, not evidence of
  wrongdoing.

## Surprises
- The old best case (EAGLE CAMPOS) was stationary. The "loitering" reading in the region scan was right, but it is
  not a dark transit.
- All 404 candidates found a scene, so the scene-time test never fell back to the day-level test. Sentinel-1 covers
  these ocean areas densely.
- Scene times cluster at a few UTC times (most often 07:54, 08:12, 08:13, 08:22, 08:30, 23:32). One pass explains
  many cells at once, which is why the top survivor has 37 candidates.

## Files
- `scripts/rerank_cases.py`: the re-rank; `--root` sets where the cached data lives.
- `data/cases/cases_v2.json`: all 109 gaps as case details (gitignored; regenerate with the script).
- `data/cases/rerank_counts.json`: the counts table above (gitignored).
- `tests/fixtures/api/case.json`: the top surviving case, in the `GET /api/cases/{id}` shape.
