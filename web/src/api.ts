/**
 * Typed client for the frozen v1 read-only API (docs/api-schema.md).
 *
 * Types mirror the schema field for field. Coordinates are [lon, lat] (GeoJSON
 * order). Times are ISO 8601 UTC strings ending in "Z". The dev server proxies
 * /api to the FastAPI app on 127.0.0.1:8000 (vite.config.ts).
 */

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
  null_ratio: number;
  null_ci: [number, number];
  recall: number;
  n_recall: number;
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
