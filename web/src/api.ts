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
