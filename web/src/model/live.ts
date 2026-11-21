/**
 * Types for the live-region API (src/darkfleet/api/live.py). Field names mirror the JSON
 * exactly. Coordinates are plain lat/lon here (the live API is not GeoJSON). Times are ISO 8601
 * UTC strings ending in "Z". Shared contract: every live module imports these types.
 */

export type EventKind = "silence" | "jump" | "clone" | "meeting" | "spoof";

export const EVENT_KINDS: readonly EventKind[] = ["silence", "jump", "clone", "meeting", "spoof"];

/** One finding. `evidence` keys differ by kind; read them defensively (they may be missing). */
export interface LiveEvent {
  kind: EventKind;
  mmsi: number;
  start: string;
  end: string;
  lat: number;
  lon: number;
  severity: number;
  explanation: string;
  other_mmsi: number | null;
  evidence: Record<string, unknown>;
}

export interface LiveWatchlistEntry {
  mmsi: number | null;
  imo: number | null;
  name: string | null;
  /** "sanctioned" | "shadow_fleet" | "detained" (detained is listed but not shown in red). */
  category: string;
  strength: string;
  entry: {
    caption: string | null;
    imo: string | null;
    risk: string[];
    url: string | null;
    datasets: string[];
  };
}

export interface LiveVessel {
  mmsi: number;
  name: string | null;
  imo: number | null;
  ship_type: number | null;
  /** Speed class, e.g. "default" or "tanker". */
  class: string;
  last: { t: string; lat: number; lon: number; sog: number } | null;
}

export interface LiveCoverageHole {
  start: string;
  end: string;
}

/** GET /api/live/<REGION>/summary: everything except events and vessels. */
export interface LiveSummary {
  region: string;
  generated_at: string;
  window: { start: string; end: string };
  coverage: {
    minutes: number;
    median_msgs_per_min: number;
    holes: LiveCoverageHole[];
  };
  /** Counts per kind plus pings, vessels and watchlist, e.g. silences, jumps, clones, meetings. */
  counts: Record<string, number>;
  /** Older snapshots send a flat list; current ones split alerts (sanctions/shadow fleet) from info. */
  watchlist: LiveWatchlistEntry[] | { alerts?: LiveWatchlistEntry[]; info?: LiveWatchlistEntry[] };
}

/** One row of GET /api/live/regions. */
export interface LiveRegion {
  region: string;
  generated_at: string | null;
  counts: Record<string, number>;
}
