# Baltic engine spec (frozen v1, Sat 21 Nov ~04:00 ET)

Contract: `src/darkfleet/core/tracks.py` (Ping, VesselInfo, CoverageMinute, Event, ship_class, mid_flag).
Input: `data/cache/ais/BALTIC_WATCH/*.jsonl.gz` (aisstream messages, one JSON per line; see
`tests/fixtures/ais/baltic_sample.jsonl`). Speed caps: `core.speeds.max_speed_knots(class)`.

## Modules, owners (one issue = one lane = one writer)
| Issue | File(s) | Public API |
|---|---|---|
| #72 | `ingest/ais_tracks.py`, `store` additions, `scripts/load_ais.py` | `parse_line(str) -> Ping \| VesselInfo \| None`; `iter_files(paths)`; `coverage(pings_or_times) -> list[CoverageMinute]`; repo tables `positions`, `vessels_static`, `coverage_minutes` (Timescale hypertable on positions.ts; SQLite fallback) |
| #73 | `core/kinematics.py` | `split_tracks(pings) -> dict[int, list[Ping]]` (sorted, deduped, drops lat/lon 0/91/181 junk); `covered(t0, t1, coverage, min_msgs_per_min=150) -> bool`; `silences(track, coverage, *, min_gap_min=60, min_sog=2.0) -> list[Event]`; `jumps(track, cap_kn, *, min_km=5.0, factor=1.5) -> list[Event]`; `clones(track, *, window_min=10, min_km=30.0, min_flips=3) -> list[Event]` |
| #75 | `core/encounters.py` | `meetings(tracks, *, max_m=500, max_sog=3.0, min_minutes=30, land=None) -> list[Event]` (pairs; grid bucketing, not O(n²)); `dark_meetings(silence_events, tracks, caps) -> list[Event]` using `core.match.rendezvous_windows` |
| #76 | `ingest/watchlist.py` | `imo_watchlist(vessels: list[VesselInfo], sanctions_csv) -> dict[int, dict]` (reuse `ingest.sanctions`; IMO match = strength "imo") |
| #74 | `core/track_validation.py`, `scripts/validate_tracks.py` | mask 1-12 h segments of underway tracks; report prism containment, bridge AUC vs displaced decoys, silence-detector recall on injected gaps, jump/clone false-positive rate on clean tracks → `docs/validation-tracks.md` + `data/validation/tracks_results.json` |
| #79 | `scripts/build_baltic.py` | runs #72-#76 over the recording → `data/snapshot/baltic.json` (shape below) |
| #80 | `api/baltic.py` (router mounted in app.py) | `GET /api/baltic/summary`, `/api/baltic/events?kind=&limit=`, `/api/baltic/vessel/{mmsi}`, `/api/baltic/live` (SSE: tail of the newest recorder file, positions + new events) |
| #81 | `web/src/screens/baltic.ts` (+ components it needs) | screen 04: live vessel dots, event markers by kind, event list, replay slider over the window |

## Event rules (defaults; every rule is a function argument so validation can sweep it)
- **silence:** consecutive pings > 60 min apart, previous SOG >= 2 kn (underway), and the recorder was
  covered the whole time (`covered`), so a gap is the ship's, not ours. Severity grows with duration and
  with distance travelled while silent. Evidence: minutes silent, last/next position, implied speed.
- **jump:** implied speed between consecutive pings > 1.5 × class cap and distance > 5 km. Evidence: km,
  minutes, implied kn, cap. Severity 1 when > 3× cap.
- **clone:** within a 10-min window the same MMSI reports positions > 30 km apart, alternating at least 3
  times (two transmitters sharing an identity). Evidence: the two position clusters, flips, max separation.
- **meeting:** two vessels within 500 m, both SOG <= 3 kn, for >= 30 min, more than 3 km from land.
- **dark_meeting:** two silence events whose prisms overlap in time and space (existing core).

## baltic.json
`{generated_at, window:{start,end}, coverage:{minutes, holes:[{start,end}]}, counts:{pings, vessels,
silences, jumps, clones, meetings, dark_meetings, watchlist}, events:[Event as dict], watchlist:[{mmsi,
imo, name, category, url}], vessels:[{mmsi, name, imo, ship_type, last:{t,lat,lon,sog}}]}`
