/**
 * Wire types for the recorded story screen (#274).
 *
 * The case is the frozen JSON written by scripts/freeze_case.py; the satcheck
 * payload is either the live answer from /api/satcheck/<alert id> or the frozen
 * scenario from /api/story/satcheck. Every field the screen prints is optional
 * on purpose: a missing endpoint must leave a readable line, never a crash.
 */

/** A dossier's list entry, as the vessel endpoint returns it (#182). */
export interface StoryWatchlist {
  category: "sanctioned" | "shadow_fleet" | string;
  list_name: string | null;
  datasets?: string[];
  url?: string | null;
}

/** The hull's details, attached where the case froze them. May be null. */
export interface StoryDossier {
  name: string;
  imo: number | null;
  flag: string | null;
  watchlist: StoryWatchlist | null;
}

export interface StoryLastFix {
  lat: number;
  lon: number;
  t: string;
  sog: number | null;
  cog: number | null;
}

/** One track point: `[lon, lat, ISO time]`. */
export type StoryTrailPoint = [number, number, string];

export interface StoryAlert {
  id: string;
  mmsi: number;
  name: string;
  label: string;
  status: string;
  t_start: string;
  last: StoryLastFix;
  /** Top speed, knots. The reach radius is this x 1.852 x hours. */
  speed_cap_kn: number;
  trail: StoryTrailPoint[];
  evidence: { minutes: number };
}

export interface StoryContact {
  mmsi: number;
  name: string | null;
  watchlist: "sanctioned" | "shadow_fleet" | string | null;
  lat: number;
  lon: number;
  t: string;
  min_dist_km: number;
  stopped: boolean;
  could_meet: boolean;
  dossier: StoryDossier | null;
}

export interface StoryBackgroundShip {
  mmsi: number;
  lat: number;
  lon: number;
}

/** `GET /api/story`: the frozen case the whole screen tells. */
export interface StoryCase {
  frozen_at: string;
  pick: { rule: string; reason: string; alert_id: string };
  alert: StoryAlert;
  /** Largest radius the speed cap allows over `alert.evidence.minutes`. */
  reach_km: number;
  ship: {
    mmsi: number;
    name: string | null;
    watchlist: string | null;
    dossier: StoryDossier | null;
  };
  contacts: StoryContact[];
  background: StoryBackgroundShip[];
  satcheck: { mode: "real" | "scenario" | string };
}

/* ---- satcheck ------------------------------------------------------------ */

export interface SatDetection {
  lat: number;
  lon: number;
  /** null means the radar target is not broadcasting; a number is the AIS match. */
  matched_mmsi: number | null;
}

export interface SatCounts {
  detected: number;
  matched: number;
  not_broadcasting: number;
}

export interface SatScene {
  time?: string;
  [key: string]: unknown;
}

/** The payload is deliberately loose: extra keys are ignored, never trusted. */
export interface SatcheckPayload {
  scene?: SatScene | null;
  dark_window?: unknown;
  detections?: unknown;
  counts?: Partial<SatCounts> | null;
  source?: unknown;
  banner?: unknown;
  sub?: unknown;
  chips?: unknown;
  chip_urls?: unknown;
}

/**
 * What beat 4 shows after the rule has run.
 *
 * `kind: "none"` is a resolved "neither endpoint answered"; null (in the
 * caller) is still pending, which is a different sentence.
 */
export interface SatcheckView {
  kind: "real" | "scenario" | "none";
  sceneTime: string | null;
  detections: SatDetection[];
  counts: SatCounts;
  source: string | null;
  banner: string | null;
  sub: string | null;
  chips: string[];
}

/* ---- the two readouts under beats 5 and 6 -------------------------------- */

/** The Pi state, reduced to the three words the screen may print. */
export type EdgeView = "ok" | "blind" | "offline";

/** The Hormuz shares, still fractions (0.319, 0.675). */
export interface TrendView {
  baseline: number;
  current: number;
}
