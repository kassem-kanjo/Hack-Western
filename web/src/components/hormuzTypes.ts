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
  /** The field name the snapshot builder writes for the median share. */
  median_dark_share?: number | null;
  median?: number | null;
  iqr?: [number, number] | null;
  /** Days the headline averaged over, and the low-coverage days it left out. */
  n_days_used?: number;
  n_days_excluded?: number;
  excluded_days?: string[];
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
 * has one and falling back to the median, then to the mean daily dark share.
 */
export function headlineShare(summary: HormuzSummary | undefined): number | null {
  if (!summary) return null;
  const value =
    summary.headline_share ??
    summary.median ??
    summary.median_dark_share ??
    summary.dark_share_mean;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.min(1, Math.max(0, value));
}

/** The hero number as a whole percent, or null when the snapshot has no share. */
export function headlinePercent(summary: HormuzSummary | undefined): number | null {
  const share = headlineShare(summary);
  return share === null ? null : Math.round(100 * share);
}

/**
 * Days the headline left out because the radar barely saw anything that day.
 * Older snapshots predate the field, so the list may be empty.
 */
export function excludedDays(summary: HormuzSummary | undefined): string[] {
  return summary?.excluded_days ?? [];
}
