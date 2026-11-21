/**
 * Shapes served by /api/hormuz/unseen and /api/hormuz/history (#88/#89).
 *
 * Types stay local to the Hormuz block: the frozen v1 API client in api.ts only
 * covers the global case file. Every field is optional except the ones the
 * console cannot draw without, so an older snapshot still renders.
 */

export interface HormuzDay {
  day: string;
  seen: number;
  broadcasting: number;
  dark: number;
  dark_share: number;
}

export interface HormuzGridCell {
  lat: number;
  lon: number;
  /** Radar detections in this cell that matched no AIS broadcast. */
  n: number;
}

export interface HormuzSummary {
  days?: number;
  seen_total?: number;
  dark_total?: number;
  dark_share_mean?: number | null;
  dark_share_ci?: [number, number] | null;
  busiest_day?: string | null;
  /** Newer snapshots may carry a robust headline and spread; all optional. */
  headline_share?: number | null;
  median?: number | null;
  iqr?: [number, number] | null;
}

export interface HormuzUnseen {
  region: string;
  period?: { start: string; end: string };
  daily: HormuzDay[];
  grid: HormuzGridCell[];
  summary?: HormuzSummary;
  source?: string;
  caveats?: string[];
}

export interface HormuzHotspot {
  lat: number;
  lon: number;
  n: number;
}

export interface HormuzHistory {
  region: string;
  period?: { start: string; end: string };
  encounters?: unknown[];
  loitering?: unknown[];
  counts?: { encounters?: number; loitering?: number; total?: number };
  hotspots: HormuzHotspot[];
  source?: string;
  caveats?: string[];
}

/**
 * The "typical day" share, preferring the robust headline when the snapshot
 * has one and falling back to the mean daily dark share.
 */
export function headlineShare(summary: HormuzSummary | undefined): number | null {
  if (!summary) return null;
  const value = summary.headline_share ?? summary.median ?? summary.dark_share_mean;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.min(1, Math.max(0, value));
}
