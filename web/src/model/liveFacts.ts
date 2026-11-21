/**
 * Turns live-API data into the exact words and numbers the Live screen shows.
 * Everything here is pure (no DOM, no fetching, no clock of its own), so the
 * screen reads as one matching set of labels no matter which component asks.
 */

import type { EventKind, LiveEvent, LiveVessel, LiveWatchlistEntry } from "./live";

/** Evidence values arrive as unknown JSON; only a real finite number is usable. */
function num(evidence: Record<string, unknown>, key: string): number | null {
  const raw = evidence[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/** "2026-11-03T14:20:00Z" -> milliseconds, or null when the string is not a time. */
function parseMs(iso: string): number | null {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

const HOUR_MS = 3_600_000;
const MINUTE_MS = 60_000;

/** Plain-English names for the five finding kinds, so chips never leak API words. */
export const KIND_LABEL: Record<EventKind, string> = {
  silence: "Silence",
  jump: "Impossible jump",
  clone: "Identity clone",
  meeting: "Meeting",
  spoof: "Spoofed position",
};

/** Most serious findings first (equal severity keeps the earlier one on top), as a new array. */
export function sortEvents(events: LiveEvent[]): LiveEvent[] {
  return [...events].sort((a, b) => {
    if (b.severity !== a.severity) return b.severity - a.severity;
    if (a.start === b.start) return 0;
    return a.start < b.start ? -1 : 1;
  });
}

/** Keeps only the kinds the current view cares about, in the order they arrived. */
export function eventsOfKinds(events: LiveEvent[], kinds: ReadonlySet<EventKind>): LiveEvent[] {
  return events.filter((event) => kinds.has(event.kind));
}

/** Trims the list to what the panel can show, so the biggest findings are the ones kept. */
export function rankRows(events: LiveEvent[], max = 7): LiveEvent[] {
  return events.slice(0, max);
}

/** How many hours the summary covers, to one decimal; a broken or missing window reads as 0. */
export function windowHours(window: { start: string; end: string }): number {
  const start = parseMs(window.start);
  const end = parseMs(window.end);
  if (start === null || end === null) return 0;
  return Math.round(((end - start) / HOUR_MS) * 10) / 10;
}

/** Total of everything unusual in the summary, so the headline number matches the list below. */
export function unusualCount(counts: Record<string, number>): number {
  const keys = ["silences", "jumps", "clones", "meetings", "spoofs"];
  return keys.reduce((sum, key) => {
    const value = counts[key];
    return typeof value === "number" && Number.isFinite(value) ? sum + value : sum;
  }, 0);
}

/** Durations the way a person says them: "6 min", "4 h 12 min" (and "2 h", not "2 h 0 min"). */
export function formatDuration(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 0) return "0 min";
  const whole = Math.round(minutes);
  if (whole < 60) return `${whole} min`;
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** When it happened, in words: "just now", "12 min ago", "2 h ago"; unreadable times show nothing. */
export function timeAgo(iso: string, nowMs: number): string {
  const then = parseMs(iso);
  if (then === null) return "";
  const diff = Math.max(0, nowMs - then);
  if (diff < MINUTE_MS) return "just now";
  if (diff < HOUR_MS) return `${Math.floor(diff / MINUTE_MS)} min ago`;
  return `${Math.floor(diff / HOUR_MS)} h ago`;
}

/** Where an instant sits on the timeline bar, 0 at the start and 1 at the end (never outside). */
export function timelineFraction(iso: string, window: { start: string; end: string }): number {
  const at = parseMs(iso);
  const start = parseMs(window.start);
  const end = parseMs(window.end);
  if (at === null || start === null || end === null) return 0;
  const span = end - start;
  if (span <= 0) return 0;
  const fraction = (at - start) / span;
  if (fraction < 0) return 0;
  if (fraction > 1) return 1;
  return fraction;
}

/**
 * The one big figure on each event card, plus its quiet follow-up phrase, so every
 * kind reads at a glance even though its evidence names differ.
 *
 * `value` is empty when the evidence does not carry the figure: the caller hides
 * the block rather than printing a dash that means nothing.
 */
export function bigNumber(event: LiveEvent): { value: string; tail: string } {
  if (event.kind === "jump") {
    const ratio = num(event.evidence, "ratio");
    if (ratio === null) return { value: "", tail: "" };
    return { value: `${Math.round(ratio)}×`, tail: "too fast" };
  }
  if (event.kind === "silence") {
    const minutes = num(event.evidence, "minutes");
    if (minutes === null) return { value: "", tail: "" };
    // Round once to whole minutes first so 239.6 min reads "4 h 0 min", never "3 h 60 min".
    const whole = Math.round(minutes);
    if (whole < 60) return { value: `${whole} min`, tail: "silent" };
    const hours = Math.floor(whole / 60);
    const rest = whole % 60;
    return { value: `${hours} h`, tail: `${rest} min silent` };
  }
  if (event.kind === "clone") {
    const km = num(event.evidence, "separation_km");
    if (km === null) return { value: "", tail: "" };
    return { value: `${Math.round(km)} km`, tail: "apart" };
  }
  if (event.kind === "meeting") {
    const minutes = num(event.evidence, "minutes");
    if (minutes === null) return { value: "", tail: "" };
    return { value: `${Math.round(minutes)} min`, tail: "together" };
  }
  // Spoof evidence names the ships: n_vessels is authoritative, the list is the fallback.
  const rawVessels = event.evidence["vessels"];
  const ships =
    num(event.evidence, "n_vessels") ??
    (Array.isArray(rawVessels) ? rawVessels.length : num(event.evidence, "count"));
  if (ships === null) return { value: "", tail: "" };
  return { value: `${Math.round(ships)} ships`, tail: "one false point" };
}

/** What the UI calls a ship the feed has no name for. Never the bare MMSI as a title. */
export const UNNAMED_VESSEL = "Unnamed vessel";

/** The name to show for a ship, falling back to a described placeholder when the feed has no name. */
export function vesselName(mmsi: number, vessels: LiveVessel[]): string {
  const vessel = vessels.find((v) => v.mmsi === mmsi);
  const name = vessel?.name?.trim();
  return name ? name : UNNAMED_VESSEL;
}

/**
 * The watchlist entry behind a flagged event, so only genuinely sanctioned or
 * shadow-fleet ships raise a red flag (detained entries are not red).
 */
/** Only sanctions and shadow-fleet hits are alerts (red); port detentions are not shown as alerts. */
export function watchlistAlerts(
  watchlist: LiveWatchlistEntry[] | { alerts?: LiveWatchlistEntry[]; info?: LiveWatchlistEntry[] } | null | undefined,
): LiveWatchlistEntry[] {
  if (!watchlist) return [];
  if (Array.isArray(watchlist)) {
    return watchlist.filter((w) => w.category === "sanctioned" || w.category === "shadow_fleet");
  }
  return watchlist.alerts ?? [];
}

/** Everything the recording listed, alerts first, whatever shape the summary sent. */
export function watchlistEntries(
  watchlist: LiveWatchlistEntry[] | { alerts?: LiveWatchlistEntry[]; info?: LiveWatchlistEntry[] } | null | undefined,
): LiveWatchlistEntry[] {
  if (!watchlist) return [];
  if (Array.isArray(watchlist)) return [...watchlist];
  return [...(watchlist.alerts ?? []), ...(watchlist.info ?? [])];
}

function isAlert(entry: LiveWatchlistEntry): boolean {
  return entry.category === "sanctioned" || entry.category === "shadow_fleet";
}

/**
 * The list entry for one vessel, if the recording matched it. An MMSI hit wins
 * over an IMO hit, and an alert wins over a detention, so the card always shows
 * the strongest reason the ship is listed.
 */
export function watchlistForVessel(
  vessel: LiveVessel,
  watchlistInput: Parameters<typeof watchlistEntries>[0],
): LiveWatchlistEntry | null {
  const entries = watchlistEntries(watchlistInput);
  const ordered = [...entries].sort((a, b) => Number(isAlert(b)) - Number(isAlert(a)));

  for (const entry of ordered) {
    if (entry.mmsi !== null && entry.mmsi === vessel.mmsi) return entry;
  }
  if (vessel.imo !== null) {
    for (const entry of ordered) {
      if (entry.imo !== null && entry.imo === vessel.imo) return entry;
    }
  }
  return null;
}

export function watchlistHit(
  event: LiveEvent,
  vessels: LiveVessel[],
  watchlistInput: Parameters<typeof watchlistAlerts>[0],
): LiveWatchlistEntry | null {
  const watchlist = watchlistAlerts(watchlistInput);
  const mmsis = new Set<number>();
  mmsis.add(event.mmsi);
  if (event.other_mmsi !== null) mmsis.add(event.other_mmsi);

  const imos = new Set<number>();
  for (const vessel of vessels) {
    if (mmsis.has(vessel.mmsi) && vessel.imo !== null) imos.add(vessel.imo);
  }

  for (const entry of watchlist) {
    if (entry.category !== "sanctioned" && entry.category !== "shadow_fleet") continue;
    if (entry.mmsi !== null && mmsis.has(entry.mmsi)) return entry;
    if (entry.imo !== null && imos.has(entry.imo)) return entry;
  }
  return null;
}

/** Message throughput with thousands separators, so "5,624" is not misread as "5624". */
export function fmtCoverage(msgsPerMin: number): string {
  return Math.round(msgsPerMin).toLocaleString("en-US");
}
