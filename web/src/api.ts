/**
 * Typed client for the frozen v1 read-only API (docs/api-schema.md).
 *
 * Types mirror the schema field for field. Coordinates are [lon, lat] (GeoJSON
 * order). Times are ISO 8601 UTC strings ending in "Z". The dev server proxies
 * /api to the FastAPI app on 127.0.0.1:8000 (vite.config.ts).
 */

import type { LiveEvent, LiveRegion, LiveSummary, LiveVessel } from "./model/live";

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

export interface Sanctions {
  match: boolean;
  source: string;
  entry: string | null;
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

/** `POST /api/edge/kill` (202): the supervisor accepted the kill request. */
export interface EdgeKillResponse {
  ok: boolean;
  requested_at: number;
}

/** Newest supervisor status, with controls and frame availability. */
export function fetchEdgeStatus(signal?: AbortSignal): Promise<EdgeStatus> {
  return getJson<EdgeStatus>("/api/edge/status", signal);
}

/**
 * URL of the latest annotated frame. `t` is a cache-buster on top of the API's
 * `Cache-Control: no-store`, so a 4 fps refresh can never show an older frame.
 */
export function edgeFrameUrl(t: number): string {
  return `/api/edge/frame.jpg?t=${Math.round(t)}`;
}

/** Ask the supervisor to kill the (untrusted) detector; 403/409 throw. */
export async function killEdgeDetector(signal?: AbortSignal): Promise<EdgeKillResponse> {
  const res = await fetch("/api/edge/kill", {
    method: "POST",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`POST /api/edge/kill failed: HTTP ${res.status}`);
  }
  return (await res.json()) as EdgeKillResponse;
}
