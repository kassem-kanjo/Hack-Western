/**
 * Typed client for the frozen v1 read-only API (docs/api-schema.md).
 *
 * Types mirror the schema field for field. Coordinates are [lon, lat] (GeoJSON
 * order). Times are ISO 8601 UTC strings ending in "Z". The dev server proxies
 * /api to the FastAPI app on 127.0.0.1:8000 (vite.config.ts).
 */

import type { LiveEvent, LiveRegion, LiveSummary, LiveVessel } from "./model/live";
// The sample watch snapshot is a local asset (no CDN): Vite rewrites this to the
// emitted file in a build and serves the source file in dev.
import watchSampleUrl from "./fixtures/watch.sample.json?url";

export type LonLat = [number, number];

export interface GeoPolygon {
  type: "Polygon";
  coordinates: number[][][];
}

export interface Vessel {
  name: string | null;
  mmsi: string | null;
  flag: string | null;
  type: string | null;
}

export interface Fix {
  t: string;
  pos: LonLat;
}

export type CaseStatus = "candidate" | "ruled_out" | "plain";
export type Band = "low" | "medium" | "high";

export interface CaseSummary {
  id: string;
  vessel: Vessel;
  off: Fix;
  on: Fix;
  hours: number;
  score: number | null;
  band: Band | null;
  status: CaseStatus;
  n_candidates: number;
  n_meetings: number;
  region_label: string | null;
}

export interface LensFrame {
  t: string;
  frac: number;
  polygon: GeoPolygon;
  /** k=2 (≈95%) Brownian-bridge contour, clipped to this frame's lens; null when undrawable. */
  likely: GeoPolygon | null;
}

export interface Candidate {
  pos: LonLat;
  cell_deg: number;
  day: string;
  scene_time: string | null;
  scene_source: string | null;
  lens_at_scene: GeoPolygon | null;
  inside: boolean | null;
  slack: number | null;
  verdict: "candidate" | "ruled_out";
  reason: string;
}

export interface Meeting {
  other_id: string;
  other_vessel: Vessel;
  start: string;
  end: string;
  max_overlap_km2: number;
  centroid: LonLat;
  slack: number;
}

export interface ScoreFactor {
  key: string;
  label: string;
  value: string;
  points: number;
  note: string;
}

/** The matched list entry. The snapshot ships an object; older builds shipped a plain string. */
export interface SanctionsEntry {
  caption?: string | null;
  imo?: string | null;
  mmsi?: string | null;
  url?: string | null;
}

export interface Sanctions {
  match: boolean;
  source: string;
  entry: SanctionsEntry | string | null;
  /** "detained" is a port-detention record, not a sanction. */
  category?: string | null;
  note?: string | null;
}

export interface Source {
  name: string;
  dataset: string;
  accessed: string;
}

export interface CaseDetail extends CaseSummary {
  speed_cap_kn: number;
  speed_basis: string;
  straight_line_kn: number;
  footprint: GeoPolygon;
  lens_frames: LensFrame[];
  candidates: Candidate[];
  meetings: Meeting[];
  score_factors: ScoreFactor[];
  sanctions: Sanctions | null;
  sources: Source[];
  caveats: string[];
}

export interface Totals {
  gaps_scanned: number;
  with_candidates: number;
  ruled_out: number;
  meetings: number;
}

export interface CasesResponse {
  generated_at: string;
  totals: Totals;
  cases: CaseSummary[];
}

export interface DarkHoursDay {
  day: string;
  hours: number;
  gaps: number;
}

/** One region/week of the radar series (weekly continuous aggregate). */
export interface SarWeek {
  week: string;
  region: string;
  seen: number;
  broadcasting: number;
  dark: number;
}

/** Position/AIS messages that landed in one UTC hour (hourly continuous aggregate). */
export interface AisHour {
  hour: string;
  msgs: number;
}

export interface Validation {
  null_ratio: number | null;
  null_ci: [number, number] | null;
  null_p: number | null;
  bridge_auc: number | null;
  bridge_auc_ci: [number, number] | null;
  bridge_radar_ratio: number | null;
  holdout_ratio: number | null;
  holdout_ci: [number, number] | null;
  verdict: string | null;
  source: string | null;
}

export interface StatsResponse {
  dark_hours_daily: DarkHoursDay[];
  /** Weekly radar series; absent on older snapshots. */
  sar_weekly?: SarWeek[];
  /** Hourly position counts; absent on older snapshots. */
  ais_hourly?: AisHour[];
  /** Backend that produced the snapshot: Tiger Data (Timescale) or the SQLite cache. */
  source?: "tiger" | "sqlite";
  validation: Validation | null;
  /** Dates the gap scan covers. */
  gap_window?: { start: string; end: string };
  /** Own-track and silence-detector results; keys are absent when the source files are. */
  own_track?: {
    hidden_route_inside?: number;
    hidden_route_total?: number;
    silence_found_receivers_up?: number;
    silence_total_receivers_up?: number;
  };
}

export type CaseStatusFilter = "candidate" | "ruled_out" | "all";

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** Ranked case summaries. Pass limit 0 or a large number to get every case. */
export function fetchCases(
  opts: { limit?: number; status?: CaseStatusFilter } = {},
  signal?: AbortSignal,
): Promise<CasesResponse> {
  const limit = opts.limit ?? 200;
  const status = opts.status ?? "all";
  return getJson<CasesResponse>(`/api/cases?limit=${limit}&status=${status}`, signal);
}

/** Full detail for one case, including lens frames and candidates. */
export function fetchCase(id: string, signal?: AbortSignal): Promise<CaseDetail> {
  return getJson<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`, signal);
}

export function fetchStats(signal?: AbortSignal): Promise<StatsResponse> {
  return getJson<StatsResponse>("/api/stats", signal);
}

// ---- Live regions (src/darkfleet/api/live.py) ----

/** Every region with a live snapshot file. */
export function fetchLiveRegions(signal?: AbortSignal): Promise<LiveRegion[]> {
  return getJson<LiveRegion[]>("/api/live/regions", signal);
}

/** Header numbers, window, coverage and watchlist for one region. */
export function fetchLiveSummary(region: string, signal?: AbortSignal): Promise<LiveSummary> {
  return getJson<LiveSummary>(`/api/live/${encodeURIComponent(region)}/summary`, signal);
}

/** Severity-ordered events for one region (the API sorts; limit caps the count). */
export function fetchLiveEvents(
  region: string,
  limit = 500,
  signal?: AbortSignal,
): Promise<LiveEvent[]> {
  return getJson<LiveEvent[]>(
    `/api/live/${encodeURIComponent(region)}/events?limit=${limit}`,
    signal,
  );
}

/** Latest fix per vessel for one region. */
export function fetchLiveVessels(region: string, signal?: AbortSignal): Promise<LiveVessel[]> {
  return getJson<LiveVessel[]>(`/api/live/${encodeURIComponent(region)}/vessels`, signal);
}

// ---- Watch (docs/watch-contract.md; issue #86) ----

/** The five suspicious kinds the watch snapshot can carry. */
export type WatchAlertKind =
  | "dark"
  | "spoof"
  | "jump"
  | "meeting"
  | "sanctioned_stop"
  | "radar_unmatched";
export type WatchAlertStatus = "ongoing" | "ended";
export type WatchAreaKind = "listening" | "watched";
export type WatchlistTag = "sanctioned" | "shadow_fleet" | null;

/** A listening area (we record here) or a watched area (we care here). */
export interface WatchArea {
  id: string;
  name: string;
  kind: WatchAreaKind;
  /** [lon_min, lat_min, lon_max, lat_max] */
  bbox: [number, number, number, number];
}

/** Latest position of one hull heard in the last two hours. */
export interface WatchShip {
  mmsi: number;
  name: string | null;
  lat: number;
  lon: number;
  t: string;
  sog: number;
  class: string;
  watchlist: WatchlistTag;
  alert_ids: string[];
}

/** The fix an alert starts from. */
export interface WatchFix {
  lat: number;
  lon: number;
  t: string;
  sog: number;
  cog: number;
}

/** The fix the ship reappeared at (dark gaps with a known end). */
export interface WatchNextFix {
  lat: number;
  lon: number;
  t: string;
}

/** One hull that was near the reach area while the alert was live. */
export interface WatchContact {
  mmsi: number;
  name: string | null;
  watchlist: WatchlistTag;
  lat: number;
  lon: number;
  t: string;
  min_dist_km: number;
  stopped: boolean;
  could_meet: boolean;
}

export interface WatchAlert {
  id: string;
  kind: WatchAlertKind;
  status: WatchAlertStatus;
  mmsi: number;
  name: string | null;
  area_id: string | null;
  severity: number;
  /** Short, already-written phrase (at most six words). */
  label: string;
  t_start: string;
  t_end: string | null;
  last: WatchFix;
  next: WatchNextFix | null;
  speed_cap_kn: number;
  contacts: WatchContact[];
  /** Own AIS path, `[lon, lat, "t"]` in time order; empty for radar alerts. */
  trail?: [number, number, string][];
  /** Kind-specific numbers; never parsed as markup. */
  evidence: Record<string, unknown>;
}

/** One stretch when our receivers were not listening. Both shapes are accepted. */
export type WatchGap = { start: string; end: string } | [string, string];

/** How fresh the recording behind the snapshot is. Absent on older snapshots. */
export interface WatchRecording {
  latest_t: string;
  lag_s: number;
  gaps?: WatchGap[];
}

export interface WatchCounts {
  ships: number;
  alerts: number;
  hidden_routine: number;
}

/** `GET /api/watch` (docs/watch-contract.md). */
export interface WatchResponse {
  generated_at: string;
  refresh_s: number;
  areas: WatchArea[];
  ships: WatchShip[];
  alerts: WatchAlert[];
  counts: WatchCounts;
  recording?: WatchRecording | null;
}

/**
 * One SSE message on `/api/watch/stream`. `generated_at` is null when the frame
 * carried no usable JSON: the event still means "the snapshot changed".
 */
export type WatchUpdate = (generatedAt: string | null) => void;

export interface WatchStream {
  close(): void;
}

/**
 * The sample snapshot used by `?fixture`: a real-shape replay of the watch
 * contract, so the screen can be opened with the API down (or on the demo
 * laptop before the recorder has run). Emitted by Vite as a local asset.
 */
export function watchFixtureUrl(): string {
  return watchSampleUrl;
}

/** The latest watch snapshot: the world view, every ship, every suspicious alert. */
export function fetchWatch(signal?: AbortSignal): Promise<WatchResponse> {
  return getJson<WatchResponse>("/api/watch", signal);
}

/**
 * Subscribe to snapshot changes. The server sends `event: update` whenever
 * watch.json is rewritten (5 s mtime poll); EventSource reconnects on its own,
 * so a dropped stream is not an error state here.
 */
export function watchStream(onUpdate: WatchUpdate): WatchStream {
  if (typeof EventSource === "undefined") return { close(): void {} };

  const source = new EventSource("/api/watch/stream");
  const handler = (event: Event): void => {
    let generatedAt: string | null = null;
    const data = (event as MessageEvent<unknown>).data;
    if (typeof data === "string") {
      try {
        const parsed = JSON.parse(data) as { generated_at?: unknown };
        generatedAt = typeof parsed.generated_at === "string" ? parsed.generated_at : null;
      } catch {
        // Not JSON: the frame still says the file changed.
      }
    }
    onUpdate(generatedAt);
  };
  source.addEventListener("update", handler);
  return {
    close(): void {
      source.removeEventListener("update", handler);
      source.close();
    },
  };
}

// ---- Vessel dossier (#182; src/darkfleet/api/vessels.py) ----

/** The list entry behind a watchlist tag: category, citation and dataset links. */
export interface VesselWatchlist {
  category: string;
  list_name: string | null;
  datasets: string[];
  url: string | null;
}

/** The hull's newest fix. `sog`/`cog` are null when the broadcast did not carry them. */
export interface VesselLastFix {
  lat: number;
  lon: number;
  t: string;
  sog: number | null;
  cog: number | null;
}

/** One drawn track point: `[lon, lat, t]`, time order, at most 200 over 12 h. */
export type VesselTrackPoint = [number, number, string];

/** The counts and window behind the track. */
export interface VesselStats {
  pings_12h: number;
  first_seen: string | null;
  last_seen: string | null;
}

/** `GET /api/vessel/{mmsi}`: one hull, joined from watch, live and the recording. */
export interface VesselDossier {
  mmsi: number;
  name: string | null;
  imo: number | null;
  callsign: string | null;
  type: string | null;
  class: string | null;
  flag: string | null;
  length_m: number | null;
  destination: string | null;
  watchlist: VesselWatchlist | null;
  last: VesselLastFix;
  track: VesselTrackPoint[];
  alerts: string[];
  stats: VesselStats;
}

/** One hull's dossier; a 404 throws like every other GET here. */
export function fetchVesselDossier(
  mmsi: number,
  signal?: AbortSignal,
): Promise<VesselDossier> {
  return getJson<VesselDossier>(`/api/vessel/${mmsi}`, signal);
}

// ---- Edge unit (src/darkfleet/api/edge.py; contract for issue #36) ----

/**
 * One supervisor event. `t_wall` is Unix seconds (the supervisor writes it);
 * the API may also expose an ISO `t`. Extra keys are ignored, not trusted.
 */
export interface EdgeEvent {
  event: string;
  t_wall?: number;
  t?: string;
  pid?: number | null;
  code?: number | null;
  recovery_ms?: number | null;
}

/**
 * Scheduling class of one edge process, as the supervisor reported it. On QNX a
 * trusted process may hold SCHED_FIFO; everywhere else it is SCHED_OTHER with a nice value.
 */
export interface EdgeSchedule {
  policy?: string;
  priority?: number;
  nice?: number;
}

/** `status.priorities`: the three processes, each with the policy it actually got. */
export interface EdgePriorities {
  safety_core?: EdgeSchedule | null;
  supervisor?: EdgeSchedule | null;
  detector?: EdgeSchedule | null;
}

/** `status.stress`: the demo load test, while it runs. */
export interface EdgeStress {
  active: boolean;
  until?: number | null;
  workers?: number;
}

/**
 * `status.safety_tick`: lateness of the safety core's own 20 Hz loop, not the
 * end-to-end frame time (that is `last_inference_ms`). Missing on older supervisors.
 */
export interface EdgeSafetyTick {
  hz?: number | null;
  p50_late_ms?: number | null;
  p99_late_ms?: number | null;
  max_late_ms?: number | null;
  late_count_60s?: number | null;
  ticks_60s?: number | null;
}

/**
 * `GET /api/edge/status`: the supervisor's status JSON plus the two fields the
 * console needs (`controls`, `frame_available`). `source` is "pi" when the API
 * relayed the Pi and "local" when it read the laptop's status file.
 */
export interface EdgeStatus {
  state: string;
  state_reason?: string | null;
  detector_alive: boolean;
  detector_pid: number | null;
  detector_restarts: number;
  last_recovery_ms: number | null;
  deadline_ms: number | null;
  deadline_misses: number;
  frames_total: number;
  alerts_total: number;
  /** Alerts processed per second over the last second (safety core). */
  frames_per_s?: number | null;
  /** Safety-check latency percentiles in milliseconds (last minute of frames). */
  latency_p50_ms?: number | null;
  latency_p99_ms?: number | null;
  /** Deadline misses inside the last 60 s, alongside the lifetime total. */
  deadline_misses_60s?: number | null;
  /** True while the detector is alive but its source has no frame yet. */
  detector_waiting?: boolean;
  /** Wall clock on the unit (Unix seconds); compare with the decision's `t`, same clock. */
  t_wall?: number;
  /** The camera helper: alive, frame rate, source. */
  camera?: { alive?: boolean; fps?: number; source?: string } | null;
  /** A warm standby detector is loaded and ready to take over. */
  standby_alive?: boolean;
  recovery_ms_history?: number[];
  priorities?: EdgePriorities | null;
  stress?: EdgeStress | null;
  /** The safety core's own loop lateness (20 Hz), not end-to-end frame time. */
  safety_tick?: EdgeSafetyTick | null;
  alarm?: EdgeAlarmStats | null;
  /** Alert shape is owned by the safety core; the patrol screen shows the count only. */
  last_alert: unknown;
  last_frame_age_ms: number | null;
  last_inference_ms: number | null;
  events: EdgeEvent[];
  /** "local" | "pi" (kept as string so an unknown producer cannot break the screen). */
  source: string;
  /** True only when EDGE_DEMO_CONTROLS=1: the kill button is allowed. */
  controls: boolean;
  /** True while the API can serve a frame at /api/edge/frame.jpg. */
  frame_available: boolean;
  /** Present when the API answered 200 but has no status file at all. */
  error?: string;
}

/** `POST /api/edge/kill` or `POST /api/edge/stress` (202): the request was accepted. */
export interface EdgeKillResponse {
  ok: boolean;
  requested_at: number;
  target?: string;
  seconds?: number;
}

/** One detection the untrusted detector drew on the frame, in pixels. */
export interface EdgeBox {
  label: string;
  /** 0..1 confidence. */
  score: number;
  xyxy: [number, number, number, number];
}

/** What the detector reported for one frame. */
export interface EdgeAi {
  engine: string;
  /** Detector-side inference time for this frame, milliseconds. */
  inference_ms: number | null;
  boxes: EdgeBox[];
  /** True in the safety core's own decision while the detector is down. */
  down?: boolean;
}

/** Where the unit says it is. */
export interface EdgePatrol {
  lat: number;
  lon: number;
  name: string;
}

/** One AIS hull heard inside the safety core's radius. */
export interface EdgeAisShip {
  mmsi: number;
  name: string;
  km: number;
}

/** The safety core's AIS picture when it made the verdict. */
export interface EdgeAis {
  nearby: EdgeAisShip[];
  radius_km: number;
  snapshot_t: string;
}

/** The safety core's alarm for one decision (see safety_core.py). Range and TTC are estimates. */
export interface EdgeAlarm {
  level: "none" | "warning" | "critical" | "blind";
  kind?: string | null;
  text?: string;
  trend?: "closing" | "opening" | "steady" | null;
  ttc_s?: number | null;
  range_m?: number | null;
  latency_ms?: number;
  deadline_ms?: number;
  on_time?: boolean;
}

/** `status.alarm`: the guarantee as numbers. */
export interface EdgeAlarmStats {
  level: string;
  text?: string;
  deadline_ms: number;
  decisions: number;
  on_time: number;
  p99_latency_ms: number | null;
  max_latency_ms: number | null;
  blind: boolean;
  blind_episodes: number;
  blind_on_time: number;
  blind_deadline_ms: number;
}

/** One arm of the priority-guard A/B. */
export interface EdgeAbArm {
  alarm_frames: number;
  on_time: number;
  late: number;
  worst_latency_ms: number | null;
  p99_latency_ms: number | null;
  worst_tick_late_ms: number | null;
  ticks_over_5ms: number | null;
  peak_inference_ms: number;
}

/** `GET /api/edge/ab`: the same alarm path under the same load, without and with the guard. */
export interface EdgeAb {
  ran_at: string;
  stress_s: number;
  deadline_ms: number;
  load: string;
  unguarded: EdgeAbArm;
  guarded: EdgeAbArm;
}

export type EdgeVerdictLevel = "alert" | "ok" | "idle";

/** The safety core's one-line decision. */
export interface EdgeVerdict {
  level: EdgeVerdictLevel;
  text: string;
}

/**
 * `<state-dir>/latest_decision.json`, rewritten atomically after every processed
 * frame and served by `GET /api/edge/decision` (local file or relayed from the Pi).
 */
export interface EdgeDecision {
  /** Wall clock at decision time: ISO 8601, or Unix seconds on older writers. */
  t: string | number;
  /** Advances once per processed frame; the frame image follows it. */
  frame_id: number;
  /** e.g. "qnxcam:5" or "net:7070". */
  source: string;
  ai: EdgeAi;
  patrol: EdgePatrol;
  ais: EdgeAis;
  verdict: EdgeVerdict;
  alarm?: EdgeAlarm;
}

/** Newest supervisor status, with controls and frame availability. */
export function fetchEdgeStatus(signal?: AbortSignal): Promise<EdgeStatus> {
  return getJson<EdgeStatus>("/api/edge/status", signal);
}

/** The latest decision (what the unit saw and what the safety core concluded). */
export function fetchEdgeDecision(signal?: AbortSignal): Promise<EdgeDecision> {
  return getJson<EdgeDecision>("/api/edge/decision", signal);
}

/** The last priority-guard A/B result; rejects (404) until one has been run. */
export function fetchEdgeAb(signal?: AbortSignal): Promise<EdgeAb> {
  return getJson<EdgeAb>("/api/edge/ab", signal);
}

/**
 * URL of the latest annotated frame. `t` is a cache-buster on top of the API's
 * `Cache-Control: no-store`, so a 4 fps refresh can never show an older frame.
 */
export function edgeFrameUrl(t: number): string {
  return `/api/edge/frame.jpg?t=${Math.round(t)}`;
}

/** Ask the supervisor to kill the (untrusted) detector; 403/502 throw. */
export async function killEdgeDetector(signal?: AbortSignal): Promise<EdgeKillResponse> {
  return postEdgeControl("kill", signal);
}

/** Ask the supervisor to simulate the AI saturating every core for ten seconds. */
export async function stressEdgeDetector(signal?: AbortSignal): Promise<EdgeKillResponse> {
  return postEdgeControl("stress", signal);
}

async function postEdgeControl(name: string, signal?: AbortSignal): Promise<EdgeKillResponse> {
  const res = await fetch(`/api/edge/${name}`, {
    method: "POST",
    headers: { Accept: "application/json", "X-Darkfleet-Control": "1" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`POST /api/edge/${name} failed: HTTP ${res.status}`);
  }
  return (await res.json()) as EdgeKillResponse;
}

// ---- Hormuz radar picture (api/hormuz.py; frozen snapshot files) ----

/** One person or hull in a GFW event. */
export interface GfwParty {
  name: string | null;
  mmsi: string | null;
  flag?: string | null;
  type?: string | null;
}

/** A GFW ship-to-ship encounter. */
export interface GfwEncounter {
  id: string;
  start: string;
  end: string;
  lat: number;
  lon: number;
  vessel: GfwParty;
  other: GfwParty;
  median_distance_km?: number;
}

export interface HormuzUnseenFile {
  daily: { day: string; seen: number; broadcasting: number; dark: number; dark_share: number }[];
  grid: { lat: number; lon: number; n: number }[];
  summary: {
    headline_share?: number | null;
    median_dark_share?: number | null;
    n_days_used?: number;
    excluded_days?: string[];
  };
}

export interface HormuzHistoryFile {
  encounters: GfwEncounter[];
  hotspots: { lat: number; lon: number; n: number }[];
  counts?: { encounters?: number; loitering?: number };
}

export function fetchHormuzUnseen(signal?: AbortSignal): Promise<HormuzUnseenFile> {
  return getJson<HormuzUnseenFile>("/api/hormuz/unseen", signal);
}

export function fetchHormuzHistory(signal?: AbortSignal): Promise<HormuzHistoryFile> {
  return getJson<HormuzHistoryFile>("/api/hormuz/history", signal);
}
