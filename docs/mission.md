# Mission: Dark Fleet (working name)

Hack Western 13 · hacking ends **Sun 22 Nov, 9:00 AM** · demo fair 11:30 AM · owner: Kassem (CEO) + Chief.

## The product in one breath
Ships that want to hide switch off their transponder (AIS). Radar satellites still see them. Dark Fleet
finds the gaps, then answers the two questions nobody else answers on a map: **where could that ship
have been while it was dark, and who could it have met?** Optional add-on: an offline edge unit
for a patrol drone/boat that flags a ship it can see but that isn't broadcasting.

## Who it's for (client language)
Sanctions-compliance teams at banks, traders and insurers; marine insurers; coast guards and fisheries
enforcement. Their failure mode: a cargo, a policy or a payment touches a ship that went dark to load
sanctioned oil or fish illegally. Today an analyst stitches this together by hand across tools.

## What wins here (and how we beat teams who also use AI)
Every team has the same models. Judges see 15-20 tables of chat wrappers on mock data. We win on things
a prompt can't produce:
1. **A real finding, not a feature list.** The demo opens on a specific ship, from real data, that went
   dark, and shows where it most likely went and who it met. Judges can look it up.
2. **A peer-reviewed method, made visible: space-time prisms** (Sharma & Shekhar, ACM TIST 2022; Hägerstrand time geography). No public tool combines prisms, radar-in-lens matching and sanctions on one map (docs/research.md). A ship last seen at A (time t0) and
   seen again at B (t1) can only have been inside the lens where two speed-limited circles overlap
   (time geography, Hägerstrand). Radar detections inside the lens are candidate sightings of that ship.
   Two dark ships whose prisms overlap in space *and* time = **possible hidden rendezvous** (ship-to-ship
   transfer) that standard encounter detection misses because neither ship was broadcasting.
   It's visual (the lens is beautiful on a map), explainable in one sentence, and provably correct.
3. **Rigour you can see.** Geometry proven with property-based tests; every flag shows *why* (factors,
   numbers, data source, caveats). Honest about lag and non-commercial data.
4. **A demo that cannot fail.** Snapshot mode serves cached real data offline; venue wifi is irrelevant.
   Rehearsed to 3:00, every number from a real run.
5. **Doesn't look vibe-coded.** Designed operations console (dense, dark, data-first), wireframed first.
6. **Mission-critical edge (if a QNX Pi appears).** AI as an untrusted process beside a small safety
   core; we kill the AI live and the device keeps alerting.

## Prize map (enter only where it's genuine)
| Prize | Why we fit | Owner of the proof |
|---|---|---|
| Overall 1st-3rd | Real finding + original algorithm + polished console | demo script |
| Most Adventurous ("discover the unknown") | Literally reveals where hidden ships went | demo opening |
| MLH Tiger Data | Positions/events/detections as hypertables; continuous aggregate drives the dashboard | `store/` |
| MLH Entire | Agent sessions, graph and Trails used for our real multi-agent build | build log |
| QNX ($1,000) | Only if we get a Pi: edge unit on QNX with `python3-onnxruntime` | `edge/` |
| MLH GoDaddy, Vultr | Domain + hosting for the live demo (free credits/codes) | deploy |
| DEV Writing ($100) | Post-hack write-up of what the data surprised us with | after deadline |
Not entering: Gemini, Auth0, Solana, TinyFish, Sun Life, Canada Life, PCBWay.

## Four hats
- **Engineer:** hard problems = prism geometry on a sphere, matching gridded radar detections, rendezvous
  search at scale, and data volume. Python for data/geo (shapely/pyproj/numpy ecosystem), TypeScript +
  MapLibre/deck.gl for the console (GPU map layers), Postgres/Timescale for time-series. Proof: property
  tests on geometry, fixtures from real API responses, exit-code gates.
- **Product:** pain = "did a ship in my deal go dark, where, and with whom?" Metric = time from question to
  defensible answer (minutes vs analyst-hours). Cut: accounts/login, global coverage (one region done
  well), mobile layout, chat UI.
- **Designer:** 10-second read = map, dark gaps as dashed lines, lens shapes, red rendezvous markers, a
  case-file panel. Presentation decoupled: tokens + components; data layer knows nothing about pixels.
- **Curator:** fits Kassem's thesis (operational products for teams whose mistakes are expensive); the
  memorable moment is the lens revealing a hidden meeting.

## Scope guardrails
- **One region, one month, done well.** Strait of Hormuz / Gulf of Oman (decided by the data probe, see
  docs/feasibility-dark-fleet.md: the Baltic has no GFW gap events and almost no SAR).
- Demo must run with no internet (snapshot) and optionally live (aisstream layer).
- Non-commercial data only; attribution "Powered by Global Fishing Watch" on every view.

## Feasibility gates (this plan is also the feasibility study)
Each gate has a test, a kill criterion and a fallback. Results go in `docs/feasibility-dark-fleet.md`.
| # | Question | Test | Pass | Fallback if it fails |
|---|---|---|---|---|
| F1 | Can we get GFW data at all? | Token + one call per dataset | 200 OK on gaps, encounters, SAR | 403 on a dataset: request access; meanwhile aisstream-only gap detection |
| F2 | Are radar detections usable as points? | 4Wings SAR query at highest resolution for region/month | Per-detection or ~1 km cells with timestamps | Daily ~10 km cells: match prisms to cells (coarser lens, still valid) |
| F3 | Enough dark activity in region/month? | Count gap events + unmatched SAR in Baltic, last 60 days | ≥ 20 gaps, ≥ 1 case with SAR inside a prism | Probe 3 other regions (Gulf of Oman, Black Sea, Argentine EEZ edge); pick the richest |
| F4 | Is the prism matcher meaningful? | Run on F3 data | ≥ 1 case where a SAR detection falls in a lens and no AIS ship explains it | Show prisms + gaps only; rendezvous becomes "possible" overlap list |
| F5 | Live layer works? | aisstream subscription for region, 10 min | Positions stream; we can flag a silence | Drop live layer; snapshot only |
| F6 | Storage | Tiger Cloud free or local Postgres | Hypertable + continuous aggregate created | SQLite/DuckDB behind the same interface; Tiger Data prize dropped |
| F7 | Edge detector | YOLOX-nano ONNX on laptop | Detects "boat" in test images < 200 ms/frame | Smaller input size; or classical CV fallback |
**Kill rule:** if F1 and F5 both fail by 11 AM Saturday (no real data source at all), switch idea; nothing
else kills the project, it only narrows it.

## Checkpoints (Saturday)
- **CP1 (9 AM briefing):** plan + data probe results + foundation running locally.
- **CP2 (~3 PM):** end-to-end: real gaps → prisms → candidate detections → rendezvous → console.
- **CP3 (~10 PM):** polish, snapshot mode, edge unit (laptop or QNX), demo rehearsed.
- **Sun 6-9 AM:** freeze, Devpost, video, submit by 8:30 AM.
