/**
 * Patrol-unit sightings on the Watch feed (#220). The patrol unit logs "vessel in view, not on
 * AIS"; the laptop API relays the log. A sighting is a patrol position, never a ship identity,
 * so the row names the unit and the time and nothing else.
 */

import { h } from "./dom";

export const SIGHTINGS_URL = "/api/edge/sightings";
export const SIGHTINGS_POLL_MS = 10_000;

export interface SightingFeedItem {
  kind: "patrol_sighting";
  id: string;
  t: string | null;
  lat: number | null;
  lon: number | null;
  title: string;
  seq: number | null;
}

export interface SightingsPayload {
  chain?: { ok?: boolean; records?: number };
  sightings: { seq?: number; patrol?: { name?: string } }[];
  feed: SightingFeedItem[];
}

/** The payload, or null when the unit has logged no sighting yet (the API answers 404). */
export async function fetchSightings(signal?: AbortSignal): Promise<SightingsPayload | null> {
  try {
    const response = await fetch(SIGHTINGS_URL, { headers: { Accept: "application/json" }, signal });
    if (!response.ok) return null;
    const body = (await response.json()) as SightingsPayload;
    return Array.isArray(body.feed) ? body : null;
  } catch {
    return null;
  }
}

/** Sequence number to patrol-unit name, from the log records (the feed item has no name). */
export function unitNames(payload: SightingsPayload): Map<number, string> {
  const names = new Map<number, string>();
  for (const record of payload.sightings) {
    if (typeof record.seq === "number" && record.patrol?.name) names.set(record.seq, record.patrol.name);
  }
  return names;
}

function clockUtc(iso: string | null): string {
  const t = iso === null ? null : new Date(iso);
  if (t === null || Number.isNaN(t.getTime())) return "";
  return `${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")} UTC`;
}

/** One sighting row: "Patrol unit Kiel Bight · 21:04 UTC · vessel in view, not on AIS". */
export function sightingRow(
  item: SightingFeedItem,
  unit: string,
  onPick: (item: SightingFeedItem) => void,
): HTMLElement {
  const glyph = h("span", { class: "watch-glyph", "aria-hidden": "true" });
  glyph.innerHTML =
    '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M8 2.5L13.5 13H2.5Z" fill="currentColor"/></svg>';
  glyph.style.color = "var(--text-1)";
  const row = h(
    "button",
    { class: "watch-row is-patrol", type: "button" },
    glyph,
    h("span", { class: "watch-row-name" }, `Patrol unit ${unit}`),
    h("span", { class: "watch-row-tag" }, "vessel in view, not on AIS"),
    h("time", { class: "watch-mono watch-row-time", datetime: item.t ?? "" }, clockUtc(item.t)),
  );
  row.addEventListener("click", () => onPick(item));
  return row;
}
