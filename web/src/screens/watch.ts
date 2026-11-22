/**
 * Watch screen: the whole world, every ship we heard, and the few events worth
 * stopping for.
 *
 * Layout is the analyst convention: events on the left, map in the middle, and
 * (once one event is open) a field table on the right. Nothing in the rail is
 * a paragraph; every row is a name, a short tag, an area and a UTC time.
 *
 * The URL is the selection (`#/watch/alert/<id>`), so a reload reproduces the
 * same view and the list, the map and the detail table cannot disagree.
 */

import "../styles/watch.css";

import type {
  WatchAlert,
  WatchAlertKind,
  WatchContact,
  WatchResponse,
  WatchShip,
  WatchStream,
} from "../api";
import {
  fetchVesselDossier,
  fetchWatch,
  watchFixtureUrl,
  watchStream,
  type VesselDossier,
} from "../api";
import type { WatchArea, WatchRecording } from "../api";
import { mountReplay, type ReplayView } from "../components/AlertReplay";
import { h } from "../components/dom";
import { guideLine } from "../components/GuideLine";
import { mountSatelliteCheck, type SatelliteCheckHandle } from "../components/SatelliteCheck";
import { onVesselFocus } from "../components/SearchBox";
import {
  SIGHTINGS_POLL_MS,
  fetchSightings,
  unitNames,
  type SightingFeedItem,
} from "../components/patrolSightings";
import { topBar } from "../components/TopBar";
import { closeDossier, openDossier, setDossierTrackRenderer } from "../components/VesselDossier";
import { fmtDec, fmtInt, fmtUtc } from "../format";
import { fetchListeningRate } from "../model/areaStats";
import { timeAgo } from "../model/liveFacts";
import { LIVE_REGIONS, openLiveFeed, type LiveFeed } from "../model/liveFeed";
import {
  GFW_URL,
  type RadarPanel,
} from "./watchHormuz";
import {
  distanceKm,
  insideReach,
  mountWatchMap,
  reachRadiusKm,
  reachTimeMs,
  watchAttribution,
  type WatchMapHandle,
} from "../map/watchMap";

/** One hash the watch screen owns. `alertId` null means the world view. */
export interface WatchRoute {
  alertId: string | null;
  /** A watched area opened from the map or the rail. */
  areaId?: string | null;
}

export interface WatchScreen {
  destroy(): void;
  /** The router calls this when only the alert part of the hash changed. */
  select(alertId: string | null, areaId?: string | null): void;
}

/**
 * "#/" and "#/watch" -> world; "#/watch/alert/<id>" -> focus. Anything else
 * returns null, so the router can hand it to another screen.
 */
export function parseWatchRoute(hash: string): WatchRoute | null {
  const path = hash.startsWith("#") ? hash.slice(1) : hash;
  if (path === "" || path === "/" || path === "/watch" || path === "/watch/") {
    return { alertId: null };
  }
  const areaMatch = /^\/watch\/area\/(.+)$/.exec(path);
  if (areaMatch !== null) {
    try {
      return { alertId: null, areaId: decodeURIComponent(areaMatch[1] as string) };
    } catch {
      return null;
    }
  }
  const match = /^\/watch\/alert\/(.+)$/.exec(path);
  if (match === null) return null;
  try {
    return { alertId: decodeURIComponent(match[1] as string) };
  } catch {
    // A malformed escape is not an alert id; the world view is the fallback.
    return null;
  }
}

/** The address of one watched area. */
export function watchAreaHref(areaId: string): string {
  return `#/watch/area/${encodeURIComponent(areaId)}`;
}

/** The address of one alert, or of the world view. */
export function watchHref(alertId: string | null): string {
  return alertId === null ? "#/watch" : `#/watch/alert/${encodeURIComponent(alertId)}`;
}

/**
 * The Hormuz area opens its own screen rather than the area panel here; the rail
 * and the router share these two strings so the row cannot drift from the route (#259).
 */
const HORMUZ_AREA_ID = "HORMUZ_STRAIT";
const HORMUZ_HREF = "#/hormuz";

/* ---- wording (pure) ------------------------------------------------------- */

/**
 * The panel's duration line. Hours and minutes, never seconds: a demo reads a
 * duration at 1 m, and "2 h 10 min" is the whole sentence.
 */
export function fmtDuration(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60_000));
  if (minutes < 1) return "under a minute";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min`;
  if (rest === 0) return `${hours} h`;
  return `${hours} h ${rest} min`;
}

/** "2026-11-21T14:32:00Z" -> "21 Nov 14:32Z": the one time format on the screen. */
function fmtUtcZ(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? `${fmtUtc(iso)}Z` : "\u2014";
}

/** The noun under each type toggle. */
/** Sightings from one unit this close together (minutes) are one visit: the unit logs every frame. */
export const SIGHTING_RUN_MINUTES = 5;

/** One patrol visit: consecutive sightings from the same unit, collapsed into one Watch row. */
export interface SightingRun {
  unit: string;
  items: SightingFeedItem[];
  first: string | null;
  last: string | null;
}

/**
 * Collapse consecutive sightings (in feed order) from the same unit into runs. A sighting joins
 * the open run when it is the same unit and no more than SIGHTING_RUN_MINUTES after the previous
 * sighting in that run; anything else starts a new run. Pure: no DOM, no clock.
 */
export function collapseSightings(items: SightingFeedItem[], names: Map<number, string>): SightingRun[] {
  const runs: SightingRun[] = [];
  let open: SightingRun | null = null;
  let openMs = Number.NaN;
  for (const item of items) {
    const unit = names.get(item.seq ?? -1) ?? "patrol";
    const ms = item.t === null ? Number.NaN : Date.parse(item.t);
    const joins =
      open !== null &&
      open.unit === unit &&
      Number.isFinite(ms) &&
      Number.isFinite(openMs) &&
      Math.abs(ms - openMs) <= SIGHTING_RUN_MINUTES * 60_000;
    if (open !== null && joins) {
      open.items.push(item);
      open.last = item.t;
    } else {
      open = { unit, items: [item], first: item.t, last: item.t };
      runs.push(open);
    }
    openMs = ms;
  }
  return runs;
}

/** "21:04 UTC" for one sighting, "21:04–21:05 UTC" for a visit that spans minutes. */
function clockRangeUtc(first: string | null, last: string | null): string {
  const clock = (iso: string | null): string => {
    const t = iso === null ? null : new Date(iso);
    if (t === null || Number.isNaN(t.getTime())) return "";
    return `${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
  };
  const start = clock(first);
  const end = clock(last);
  if (start === "") return "";
  return end === "" || end === start ? `${start} UTC` : `${start}–${end} UTC`;
}

/** One collapsed patrol row: "Patrol unit Kiel Bight · vessel in view, not broadcasting its position · seen 12×, 21:04–21:05 UTC". */
function sightingRunRow(run: SightingRun, onPick: (item: SightingFeedItem) => void): HTMLElement {
  const glyph = h("span", { class: "watch-glyph", "aria-hidden": "true" });
  glyph.innerHTML =
    '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M8 2.5L13.5 13H2.5Z" fill="currentColor"/></svg>';
  glyph.style.color = "var(--text-1)";
  const count = run.items.length;
  const range = clockRangeUtc(run.first, run.last);
  const when = count > 1 ? `seen ${count}×, ${range}` : range;
  const row = h(
    "button",
    { class: "watch-row is-patrol", type: "button" },
    glyph,
    h("span", { class: "watch-row-name" }, `Patrol unit ${run.unit}`),
    h("span", { class: "watch-row-tag" }, "vessel in view, not broadcasting its position"),
    h("time", { class: "watch-mono watch-row-time", datetime: run.first ?? "" }, when),
  );
  const latest = run.items[run.items.length - 1];
  row.addEventListener("click", () => onPick(latest));
  return row;
}

const KIND_NAME: Record<WatchAlertKind, string> = {
  dark: "Switched off",
  sanctioned_stop: "Stopped at sea",
  spoof: "Faked position",
  meeting: "Meeting",
  jump: "Jump",
  radar_unmatched: "Seen by satellite radar",
};

/** Chip tooltips for the two kinds whose short chip names need a plain gloss. */
const KIND_TIP: Partial<Record<WatchAlertKind, string>> = {
  sanctioned_stop: "Sanctioned ship stopped at sea",
  spoof: "Reported a position it couldn't have reached",
};

/** Short filter-chip labels; the row tag keeps the full kind name. */
const KIND_CHIP: Record<WatchAlertKind, string> = {
  dark: "Switched off",
  sanctioned_stop: "Stopped",
  spoof: "Faked position",
  meeting: "Meeting",
  jump: "Jump",
  radar_unmatched: "Radar",
};

/** Glyph shapes: shape carries the kind, colour only repeats it (readable in greyscale). */
const GLYPH_SVG: Record<WatchAlertKind, string> = {
  dark: '<circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="24 8" transform="rotate(-60 8 8)"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/>',
  sanctioned_stop: '<rect x="3" y="3" width="10" height="10" fill="currentColor"/>',
  spoof: '<rect x="3" y="3" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3 3L13 13M13 3L3 13" stroke="currentColor" stroke-width="1.4"/>',
  meeting: '<circle cx="6" cy="8" r="3.4" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="8" r="3.4" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  jump: '<path d="M3.5 11.5L8 4.5L12.5 11.5" fill="none" stroke="currentColor" stroke-width="1.6"/>',
  radar_unmatched: '<path d="M8 2.5L13.5 8L8 13.5L2.5 8Z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="8" r="1.1" fill="currentColor"/>',
};

const GLYPH_COLOR: Record<WatchAlertKind, string> = {
  dark: "var(--st-dark)",
  sanctioned_stop: "var(--st-sanctioned)",
  spoof: "var(--st-spoof)",
  meeting: "var(--text-1)",
  jump: "var(--text-2)",
  radar_unmatched: "var(--st-radar)",
};

/** The kind glyph as a 16 px SVG. The markup is a constant above, never user data. */
function kindGlyph(kind: WatchAlertKind): HTMLElement {
  const span = h("span", { class: "watch-glyph", "aria-hidden": "true" });
  span.style.color = GLYPH_COLOR[kind];
  span.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16">${GLYPH_SVG[kind]}</svg>`;
  return span;
}

/** Toggle order: what Kassem asked for first, then the remaining contract kind. */
const KIND_ORDER: WatchAlertKind[] = [
  "dark",
  "sanctioned_stop",
  "spoof",
  "meeting",
  "jump",
];

/** "2026-11-14" -> "14 Nov": the radar day, short enough for a row. */
function radarDay(day: string): string {
  return fmtUtc(`${day}T00:00:00Z`).slice(0, 6);
}

/** A vessel name, or the contract's honest placeholder. */
function alertName(alert: WatchAlert): string {
  // A radar alert is fleet-level: it names no hull, so the row says what it saw.
  if (alert.kind === "radar_unmatched") {
    const n = alert.evidence["unmatched"];
    if (typeof n !== "number") return shortLabel(alert.label);
    // The date keeps this apart from the Hormuz "today" count.
    const day = alert.evidence["day"];
    const when = typeof day === "string" && Number.isFinite(Date.parse(`${day}T00:00:00Z`)) ? ` on ${radarDay(day)}` : "";
    return `${fmtInt(n)} seen by radar${when}, not broadcasting`;
  }
  const trimmed = alert.name?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : `Unnamed \u00b7 MMSI ${alert.mmsi}`;
}

/** The one line at the top of every event panel: what happened (issue #270). */
function headlineOf(alert: WatchAlert): string {
  if (alert.kind === "sanctioned_stop") return "Sanctioned ship stopped at sea";
  return plainLabel(alert.label);
}

/**
 * The two dark-alert counts the build writes (issue #270): hulls inside the reach, and
 * how many of them could have met the dark ship. Null on a snapshot built before them.
 */
function reachCounts(alert: WatchAlert): { inside: number | null; couldMeet: number | null } {
  const row = alert as WatchAlert & { inside_reach?: number | null; could_meet_count?: number | null };
  return { inside: row.inside_reach ?? null, couldMeet: row.could_meet_count ?? null };
}

/**
 * The sanctions answer for the event's own hull, from the same dossier the vessel panel
 * shows (`/api/vessel/{mmsi}`), so the two cannot disagree.
 */
function sanctionsView(dossier: VesselDossier): { cls: string; nodes: Node[] } {
  const entry = dossier.watchlist;
  if (entry === null) return { cls: "is-no", nodes: [document.createTextNode("On a sanctions list: No")] };
  const shadow = entry.category === "shadow_fleet";
  const head = shadow ? "Shadow-fleet list" : "On a sanctions list: Yes";
  const parts: string[] = [];
  if (entry.list_name !== null) parts.push(entry.list_name);
  if (dossier.imo !== null) parts.push(`matched by IMO ${dossier.imo}`);
  const text = [head, ...parts].join(" \u00b7 ");
  const first = entry.datasets[0];
  const link =
    entry.url ?? (first !== undefined ? `https://www.opensanctions.org/datasets/${encodeURIComponent(first)}/` : null);
  const nodes: Node[] = [document.createTextNode(text)];
  if (link !== null) {
    nodes.push(
      document.createTextNode(" \u00b7 "),
      h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, "Source \u2197"),
    );
  }
  return { cls: shadow ? "is-shadow" : "is-yes", nodes };
}

/** Fills the sanctions line once the dossier answers; a panel that was replaced is left alone. */
function loadSanctions(mmsi: number, el: HTMLElement): void {
  fetchVesselDossier(mmsi)
    .then((dossier) => {
      if (!el.isConnected) return;
      const view = sanctionsView(dossier);
      el.className = `watch-sanctions ${view.cls}`;
      el.replaceChildren(...view.nodes);
    })
    .catch(() => {
      if (!el.isConnected) return;
      el.className = "watch-sanctions is-pending";
      el.textContent = "Sanctions lists could not be checked right now.";
    });
}

function contactName(contact: WatchContact): string {
  const trimmed = contact.name?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : `Unnamed \u00b7 MMSI ${contact.mmsi}`;
}

/** The backend label in plain words, whole (the event headline needs all of it, #270). */
function plainLabel(label: string): string {
  // Backend labels still say "Watchlisted" and "went dark"; the screen uses plain words (QA #229 copy).
  const plain = label
    .replace(/Watchlisted ship went dark/i, "Sanctioned or shadow-fleet ship switched off")
    .replace(/went dark/i, "switched off")
    .replace(/Watchlisted/i, "Sanctioned or shadow-fleet");
  return plain.trim().split(/\s+/).filter(Boolean).join(" ");
}

/** Backend labels are already short; this makes the six-word rule structural. */
function shortLabel(label: string): string {
  return plainLabel(label).split(" ").slice(0, 6).join(" ");
}

function areaName(data: WatchResponse, areaId: string | null): string {
  if (areaId === null) return "No area";
  return data.areas.find((area) => area.id === areaId)?.name ?? areaId;
}

/** Happening now first, then earlier; most recent first inside each group. */
function sortedAlerts(alerts: WatchAlert[]): WatchAlert[] {
  return [...alerts].sort((a, b) => {
    const aLive = a.status === "ongoing" ? 0 : 1;
    const bLive = b.status === "ongoing" ? 0 : 1;
    if (aLive !== bLive) return aLive - bLive;
    const aStart = Date.parse(a.t_start);
    const bStart = Date.parse(b.t_start);
    if (Number.isFinite(aStart) && Number.isFinite(bStart) && aStart !== bStart) {
      return bStart - aStart;
    }
    return 0;
  });
}

/** Set once per page load: the cold-open lens never repeats after the reader has chosen. */
let lensUsed = false;

/**
 * The switch-off with the most ships it could have met, the same rule as Present's
 * `strongestAlert` (issue #249; mirrored because that helper is private to present.ts):
 * most could-meet contacts, then ongoing before ended, then the newest.
 */
function strongestSwitchOff(alerts: readonly WatchAlert[]): WatchAlert | null {
  const met = (alert: WatchAlert): number => alert.contacts.filter((contact) => contact.could_meet === true).length;
  const dark = alerts
    .filter((alert) => alert.kind === "dark")
    .sort(
      (a, b) =>
        met(b) - met(a) ||
        Number(b.status === "ongoing") - Number(a.status === "ongoing") ||
        Date.parse(b.t_start) - Date.parse(a.t_start),
    );
  return dark[0] ?? null;
}

/**
 * The story a cold open selects (QA #229 A4: always select one, even when something is ongoing):
 *   1. the switch-off with the most ships it could have met (issue #249),
 *   2. the newest ongoing sanctioned stop,
 *   3. the newest sanctioned stop, then the newest spoof, then the newest alert of any kind.
 * Null only when there are no alerts at all.
 */
function pickLens(snapshot: WatchResponse): WatchAlert | null {
  const ongoing = sortedAlerts(snapshot.alerts).filter((alert) => alert.status === "ongoing");
  const earlier = sortedAlerts(snapshot.alerts);
  return (
    strongestSwitchOff(snapshot.alerts) ??
    ongoing.find((alert) => alert.kind === "sanctioned_stop") ??
    earlier.find((alert) => alert.kind === "sanctioned_stop") ??
    earlier.find((alert) => alert.kind === "spoof") ??
    earlier[0] ??
    null
  );
}

/** The camera opens on the first alert's area; the world view stays one crumb away. */
function openingBox(snapshot: WatchResponse): [number, number, number, number] | null {
  const first = sortedAlerts(snapshot.alerts).find((alert) => alert.area_id !== null);
  return snapshot.areas.find((area) => area.id === first?.area_id)?.bbox ?? null;
}

/**
 * One large figure with its plain label under it, and a one-line caption that says what the
 * number measures (the focus panel's top row).
 */
function figure(value: string, label: string, caption: string, title?: string): HTMLElement {
  return h(
    "div",
    { class: "watch-fig" },
    h("span", { class: "watch-fig-value", title: title ?? null }, value),
    h("span", { class: "watch-fig-label" }, label),
    h("span", { class: "watch-fig-caption" }, caption),
  );
}

/** How long an alert has been (or was) live, for the detail table. */
function durationMs(alert: WatchAlert, nowMs: number): number {
  const start = Date.parse(alert.t_start);
  if (!Number.isFinite(start)) return 0;
  const end = alert.t_end === null ? nowMs : Date.parse(alert.t_end);
  return Math.max(0, (Number.isFinite(end) ? end : nowMs) - start);
}

/**
 * Nearby hulls: the ones that could meet first, then sanctions hits, then the
 * closest. Red is the sanctions hit, so it should not automatically outrank
 * the rows that answer "who could it have met".
 */
function sortedContacts(contacts: WatchContact[]): WatchContact[] {
  return [...contacts].sort((a, b) => {
    const aM = a.could_meet === true ? 1 : 0;
    const bM = b.could_meet === true ? 1 : 0;
    if (aM !== bM) return bM - aM;
    const aS = onSanctionsList(a) ? 1 : 0;
    const bS = onSanctionsList(b) ? 1 : 0;
    if (aS !== bS) return bS - aS;
    return a.min_dist_km - b.min_dist_km;
  });
}

/** Ships in the reach area that are neither the alert's hull nor a listed contact. */
function otherShipsInReach(data: WatchResponse, alert: WatchAlert, atMs: number): number {
  const skip = new Set<number>([alert.mmsi]);
  for (const contact of alert.contacts) skip.add(contact.mmsi);
  let count = 0;
  for (const ship of data.ships) {
    if (skip.has(ship.mmsi)) continue;
    if (insideReach(alert, ship.lat, ship.lon, atMs)) count += 1;
  }
  return count;
}

/** The ship record behind a contact row, when the snapshot has heard it lately. */
function contactShip(data: WatchResponse, contact: WatchContact): WatchShip | null {
  return data.ships.find((ship) => ship.mmsi === contact.mmsi) ?? null;
}

/* ---- identity ------------------------------------------------------------- */

/**
 * The 40-odd maritime identification digits a demo is actually likely to see.
 * MID is the first three digits of an MMSI; unknown is an honest dash.
 */
const MID_COUNTRY: Record<number, string> = {
  205: "Belgium",
  209: "Cyprus",
  210: "Cyprus",
  211: "Germany",
  212: "Cyprus",
  218: "Germany",
  219: "Denmark",
  220: "Denmark",
  224: "Spain",
  225: "Spain",
  226: "France",
  227: "France",
  228: "France",
  232: "United Kingdom",
  233: "United Kingdom",
  234: "United Kingdom",
  235: "United Kingdom",
  236: "Gibraltar",
  237: "Greece",
  238: "Croatia",
  239: "Greece",
  240: "Greece",
  241: "Greece",
  244: "Netherlands",
  245: "Netherlands",
  246: "Netherlands",
  247: "Italy",
  248: "Malta",
  249: "Malta",
  250: "Ireland",
  255: "Portugal",
  256: "Malta",
  257: "Norway",
  258: "Norway",
  259: "Norway",
  261: "Poland",
  263: "Portugal",
  265: "Sweden",
  266: "Sweden",
  271: "Turkey",
  273: "Russia",
  275: "Latvia",
  276: "Estonia",
  277: "Lithuania",
  305: "Antigua and Barbuda",
  308: "Bahamas",
  309: "Bahamas",
  310: "Bermuda",
  311: "Bahamas",
  316: "Canada",
  319: "Cayman Islands",
  338: "United States",
  345: "Mexico",
  351: "Panama",
  352: "Panama",
  353: "Panama",
  354: "Panama",
  355: "Panama",
  356: "Panama",
  357: "Panama",
  366: "United States",
  367: "United States",
  368: "United States",
  369: "United States",
  370: "Panama",
  371: "Panama",
  372: "Panama",
  373: "Panama",
  374: "Panama",
  375: "Saint Vincent and the Grenadines",
  376: "Saint Vincent and the Grenadines",
  377: "Saint Vincent and the Grenadines",
  378: "British Virgin Islands",
  379: "US Virgin Islands",
  403: "Saudi Arabia",
  412: "China",
  413: "China",
  414: "China",
  416: "Taiwan",
  419: "India",
  422: "Iran",
  425: "Iraq",
  428: "Israel",
  431: "Japan",
  432: "Japan",
  440: "South Korea",
  441: "South Korea",
  447: "Kuwait",
  450: "Lebanon",
  461: "Oman",
  466: "Qatar",
  468: "Syria",
  470: "United Arab Emirates",
  477: "Hong Kong",
  503: "Australia",
  512: "New Zealand",
  525: "Indonesia",
  533: "Malaysia",
  538: "Marshall Islands",
  548: "Philippines",
  563: "Singapore",
  564: "Singapore",
  565: "Singapore",
  566: "Singapore",
  574: "Vietnam",
  601: "South Africa",
  603: "Angola",
  605: "Algeria",
  622: "Egypt",
  636: "Liberia",
  642: "Libya",
  657: "Nigeria",
  667: "Sierra Leone",
  671: "Togo",
  677: "Tanzania",
  710: "Brazil",
  725: "Chile",
  735: "Ecuador",
  760: "Peru",
};

/** MMSI -> flag state via its MID, or "No flag broadcast" when the MID is not known. */
function flagOf(mmsi: number): string {
  if (!Number.isFinite(mmsi)) return "No flag broadcast";
  const mid = Math.floor(mmsi / 1_000_000);
  return MID_COUNTRY[mid] ?? "No flag broadcast";
}

/** The alert's IMO number when the evidence carries one; otherwise omit the field. */
function imoOf(alert: WatchAlert): string | null {
  const raw = alert.evidence["imo"];
  if (typeof raw === "string" && raw.trim().length > 0) return raw.trim();
  if (typeof raw === "number" && Number.isFinite(raw)) return String(raw);
  return null;
}

/** "tanker" -> "Tanker"; no record -> an honest dash. */
function shipType(data: WatchResponse, alert: WatchAlert): string {
  const cls = data.ships.find((ship) => ship.mmsi === alert.mmsi)?.class;
  if (cls === undefined || cls.trim().length === 0) return "Not recorded";
  return cls.charAt(0).toUpperCase() + cls.slice(1);
}

/**
 * The one test for "on a sanctions list". The count in the detail section and the
 * table's Yes/No cell both read this, so they cannot disagree (issue #259).
 */
function onSanctionsList(contact: WatchContact): boolean {
  return contact.watchlist === "sanctioned";
}

/** The cell under "On a sanctions list": the answer, nothing else. */
function sanctionsListLabel(contact: WatchContact): string {
  return onSanctionsList(contact) ? "Yes" : "No";
}

/* ---- filters -------------------------------------------------------------- */

/** 6 h or 12 h, measured back from the snapshot's generated_at. */
type WindowHours = 6 | 12;

interface FilterState {
  /** Free text over name, MMSI and label. */
  query: string;
  kinds: Set<WatchAlertKind>;
  areaId: string | null;
  windowH: WindowHours;
}

/** The snapshot is the clock: relative labels and the window follow its stamp. */
function snapshotNow(data: WatchResponse): number {
  const ms = Date.parse(data.generated_at);
  return Number.isFinite(ms) ? ms : Date.now();
}

function inWindow(alert: WatchAlert, nowMs: number, hours: WindowHours): boolean {
  // The radar alert covers a whole UTC day, usually older than the window.
  if (alert.kind === "radar_unmatched") return true;
  const start = Date.parse(alert.t_start);
  if (!Number.isFinite(start)) return true;
  return start >= nowMs - hours * 3_600_000;
}

/** Area and window only: the base set the type counts are drawn from. */
function scopedAlerts(data: WatchResponse, state: FilterState): WatchAlert[] {
  const now = snapshotNow(data);
  return data.alerts.filter((alert) => {
    if (state.areaId !== null && alert.area_id !== state.areaId) return false;
    if (state.query !== "") {
      const hay = `${alertName(alert)} ${alert.mmsi} ${alert.label}`.toLowerCase();
      if (!hay.includes(state.query)) return false;
    }
    return inWindow(alert, now, state.windowH);
  });
}

function visibleAlerts(data: WatchResponse, state: FilterState): WatchAlert[] {
  // The radar picture lives on the Hormuz screen, so its alert is no longer a list row (#249).
  return sortedAlerts(
    scopedAlerts(data, state).filter((alert) => alert.kind !== "radar_unmatched" && state.kinds.has(alert.kind)),
  );
}

function kindCounts(alerts: WatchAlert[]): Map<WatchAlertKind, number> {
  const counts = new Map<WatchAlertKind, number>();
  for (const alert of alerts) counts.set(alert.kind, (counts.get(alert.kind) ?? 0) + 1);
  return counts;
}

/* ---- CSV ------------------------------------------------------------------ */

function csvCell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** The visible rows, nothing else: what the analyst is looking at is the export. */
function downloadCsv(rows: WatchAlert[], data: WatchResponse): void {
  const header = [
    "vessel",
    "mmsi",
    "type",
    "area",
    "started_utc",
    "ended_utc",
    "status",
    "severity",
  ];
  const lines = rows.map((alert) =>
    [
      alertName(alert),
      String(alert.mmsi),
      shortLabel(alert.label),
      areaName(data, alert.area_id),
      alert.t_start,
      alert.t_end ?? "",
      alert.status,
      alert.severity.toFixed(2),
    ]
      .map(csvCell)
      .join(","),
  );
  const csv = [header.join(","), ...lines].join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = h("a", { href: url, download: `watch-events-${data.generated_at.slice(0, 10)}.csv` });
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/* ---- field helpers -------------------------------------------------------- */

function field(label: string, value: string | HTMLElement, extra?: HTMLElement | null): HTMLElement[] {
  return [h("dt", null, label), h("dd", null, value, extra ?? null)];
}

/** A UTC time with its quiet relative follow-up. */
function timeValue(iso: string, nowMs: number): HTMLElement[] {
  return [
    h("time", { class: "watch-mono", datetime: iso, title: "UTC (the Z means UTC time)" }, fmtUtcZ(iso)),
    h("span", { class: "watch-rel" }, timeAgo(iso, nowMs)),
  ];
}


/** "2026-11-14T00:00:00Z" -> "14 Nov". */
function fmtDayLabel(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "\u2014";
  const d = new Date(ms);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]}`;
}


/** The ships that told the false point: where each is now, and how far off its report was. */
function spoofContacts(snapshot: WatchResponse, alert: WatchAlert): WatchContact[] {
  const raw = alert.evidence["vessels"];
  if (!Array.isArray(raw)) return [];
  const byMmsi = new Map(snapshot.ships.map((ship) => [ship.mmsi, ship]));
  const out: WatchContact[] = [];
  for (const entry of raw as { mmsi?: number; offset_km?: number }[]) {
    if (typeof entry.mmsi !== "number") continue;
    const ship = byMmsi.get(entry.mmsi);
    if (ship === undefined) continue;
    out.push({
      mmsi: ship.mmsi,
      name: ship.name,
      watchlist: ship.watchlist,
      lat: ship.lat,
      lon: ship.lon,
      t: ship.t,
      min_dist_km: typeof entry.offset_km === "number" ? entry.offset_km : 0,
      stopped: ship.sog <= 3,
      could_meet: false,
    });
  }
  return out;
}

/* ---- freshness and recording gaps (pure) ----------------------------------- */

/** Past this, the recorder is behind and the chip says so. */
const DELAYED_MS = 15 * 60_000;

/** A stopped ship has no reach area, so its nearby table and map contacts use the stated 10 km. */
const STOP_NEARBY_KM = 10;

/**
 * How far behind real time the newest recorded message is: the lag the build
 * measured, plus the time since the build.
 */
function recordingLagMs(snapshot: WatchResponse, nowMs: number): number {
  const built = Date.parse(snapshot.generated_at);
  const sinceBuild = Number.isFinite(built) ? Math.max(0, nowMs - built) : 0;
  const rec = snapshot.recording;
  const lag = rec !== null && rec !== undefined && Number.isFinite(rec.lag_s) ? rec.lag_s * 1000 : 0;
  return lag + sinceBuild;
}

function gapBounds(gap: NonNullable<WatchRecording["gaps"]>[number]): [number, number] | null {
  const [a, b] = Array.isArray(gap) ? gap : [gap.start, gap.end];
  const start = Date.parse(a);
  const end = Date.parse(b);
  return Number.isFinite(start) && Number.isFinite(end) ? [start, end] : null;
}

/** The first recording gap that overlaps the alert's window, as clock times. */
function overlappingGap(
  snapshot: WatchResponse,
  alert: WatchAlert,
  nowMs: number,
): { from: string; to: string } | null {
  const start = Date.parse(alert.t_start);
  if (!Number.isFinite(start)) return null;
  const end = alert.t_end === null ? nowMs : Date.parse(alert.t_end);
  for (const gap of snapshot.recording?.gaps ?? []) {
    const bounds = gapBounds(gap);
    if (bounds === null) continue;
    if (bounds[0] < end && bounds[1] > start) {
      const hm = (ms: number): string => new Date(ms).toISOString().slice(11, 16);
      return { from: hm(bounds[0]), to: hm(bounds[1]) };
    }
  }
  return null;
}

/**
 * Hulls heard recently, watchlisted ones ahead, then nearest; the list for a stopped ship.
 * Only hulls inside the radius the event checked are listed (issue #249): the table used to show
 * ships 9,949 km away under a "Distance checked 3 km" label. `within` is the count that passed.
 */
function nearbyShips(
  snapshot: WatchResponse,
  alert: WatchAlert,
  limit: number,
  radiusKm: number,
): { shown: WatchContact[]; within: number } {
  const out: WatchContact[] = [];
  for (const ship of snapshot.ships) {
    if (ship.mmsi === alert.mmsi) continue;
    if ((ship as { valid?: boolean }).valid === false) continue;
    out.push({
      mmsi: ship.mmsi,
      name: ship.name,
      watchlist: ship.watchlist,
      lat: ship.lat,
      lon: ship.lon,
      t: ship.t,
      min_dist_km: distanceKm(alert.last.lat, alert.last.lon, ship.lat, ship.lon),
      stopped: ship.sog <= 3,
      could_meet: false,
    });
  }
  out.sort((a, b) => {
    const aW = a.watchlist !== null ? 1 : 0;
    const bW = b.watchlist !== null ? 1 : 0;
    if (aW !== bW) return bW - aW;
    return a.min_dist_km - b.min_dist_km;
  });
  const inside = out.filter((contact) => contact.min_dist_km <= radiusKm);
  return { shown: inside.slice(0, limit), within: inside.length };
}

/** The hull names the rail's search matches against, in one place. */
function skeletonRows(count: number): HTMLElement[] {
  return Array.from({ length: count }, () =>
    h(
      "li",
      { class: "watch-skel", "aria-hidden": "true" },
      h("span", { class: "watch-skel-bar is-wide" }),
      h("span", { class: "watch-skel-bar is-narrow" }),
    ),
  );
}

/* ---- screen --------------------------------------------------------------- */

/**
 * Paint the watch screen into `app`. The whole shell (bar, map, events rail,
 * status bar) exists before the data does, with skeleton rows where the events
 * will land, so nothing moves when the snapshot arrives.
 */
export function renderWatch(app: HTMLElement, route: WatchRoute): WatchScreen {
  const abort = new AbortController();
  /** `?fixture` opens the sample snapshot instead of the API. Nothing else does. */
  const fixtureMode = new URLSearchParams(location.search).has("fixture");
  let destroyed = false;

  const mapDiv = h("div", { class: "watch-map" });
  const liveChip = h("div", { class: "watch-live", role: "status" }, "Live");
  let attrib = watchAttribution();
  const stage = h("section", { class: "stage watch-stage" }, mapDiv, liveChip, attrib);

  const eventsCount = h("span", { class: "watch-count" }, "—");
  const filtersEl = h("div", { class: "watch-filters is-loading" });
  const rowsEl = h("ol", { class: "watch-rows" }, ...skeletonRows(8));
  const areasList = h("ol", { class: "watch-areas-list" });
  const areasCount = h("span", { class: "watch-count" });
  const areasEl = h(
    "section",
    { class: "watch-areas" },
    h("div", { class: "watch-events-head is-sub" }, h("h2", null, "Watched areas"), areasCount),
    areasList,
  );
  const hiddenRoutine = h("span", { class: "watch-foot-hidden" });
  const exportBtn = h("button", { class: "watch-export", type: "button" }, "Export CSV");
  const footEl = h(
    "div",
    { class: "watch-foot" },
    hiddenRoutine,
    h("span", { class: "watch-foot-sep", "aria-hidden": "true" }, "·"),
    exportBtn,
  );
  const sightingsEl = h("ul", { class: "watch-sightings", hidden: true });
  let sightingsTimer: number | null = null;
  const events = h(
    "aside",
    { class: "watch-events" },
    h("div", { class: "watch-events-head" }, h("h2", null, "Events"), eventsCount),
    filtersEl,
    sightingsEl,
    rowsEl,
    areasEl,
    footEl,
  );

  const detailEmpty = h(
    "div",
    { class: "watch-none watch-details-empty" },
    "Click an event on the left to see where the ship could have gone.",
  );
  const detailBody = h("div", { class: "watch-details-body" }, detailEmpty);
  const detailsTitle = h("span", { class: "watch-details-title" }, "Event details");
  const back = h(
    "button",
    { class: "watch-back", type: "button", "aria-label": "Back to events" },
    "← Events",
  );
  // A soft fade with "More below" while the panel has content under the fold; it hides at the end.
  const moreCue = h("div", { class: "watch-more-cue", "aria-hidden": "true" }, "More below");
  const details = h(
    "aside",
    // #254: the panel stays on screen; with nothing selected it says what to click.
    { class: "watch-details" },
    h("div", { class: "watch-details-head" }, back, detailsTitle),
    detailBody,
    moreCue,
  );
  /** Show the cue only while content continues below the visible part of the panel. */
  function updateMoreCue(): void {
    const below = details.scrollHeight - details.clientHeight - details.scrollTop;
    moreCue.classList.toggle("is-shown", below > 8);
  }
  details.addEventListener("scroll", updateMoreCue, { passive: true });
  window.addEventListener("resize", updateMoreCue);

  // Status bar: where the data comes from, how fresh it is, and the keys.
  const statusSnap = h("span", { class: "watch-mono" }, "—");
  const statusFeed = h("span", { class: "watch-mono" }, "—");
  const statusClock = h("span", { class: "watch-mono" }, "—");
  const status = h(
    "footer",
    { class: "watch-status" },
    h(
      "span",
      { class: "watch-status-item" },
      h("span", { class: "watch-status-k" }, "Sources"),
      "Own AIS recording · ",
      h("a", { href: GFW_URL, target: "_blank", rel: "noopener noreferrer" }, "Global Fishing Watch"),
      " · ",
      h(
        "a",
        { href: "https://www.opensanctions.org", target: "_blank", rel: "noopener noreferrer" },
        "OpenSanctions",
      ),
    ),
    // Issue #249: the bar carries sources and the UTC clock only.
    h("span", { class: "watch-status-item is-clock" }, h("span", { class: "watch-status-k" }, "UTC"), statusClock),
  );

  function keyHint(key: string): HTMLElement {
    return h("kbd", { class: "watch-kbd" }, key);
  }

  const main = h("main", { class: "main watch-main" }, stage, events, details, status);
  const screen = h(
    "div",
    { class: "watch-screen" },
    guideLine(
      "watch",
      "Live: ships that switched off their transponder. Click one to see where it could have gone and who it could have met.",
    ),
    main,
  );

  app.replaceChildren(
    topBar({ active: "watch", mode: "LIVE", generatedAt: null, caseHref: null, demoHref: null }),
    screen,
  );

  let data: WatchResponse | null = null;
  let selectedId: string | null = route.alertId;
  let selectedAreaId: string | null = route.areaId ?? null;
  /** What the panel and map currently show, so a refresh does not rebuild a running view. */
  let appliedKey: string | null = null;
  let map: WatchMapHandle | null = null;
  /** #185: the dark-period scrubber, kept across a same-alert refresh so the playhead stays put. */
  let replay: ReplayView | null = null;
  let replayAlertId: string | null = null;
  /** #268: the satellite check for the open dark alert, and what its dots belong to. */
  let satcheck: SatelliteCheckHandle | null = null;
  let satcheckAlertId: string | null = null;
  /** The check's own stamp: an unchanged refresh does not redraw the map dots. */
  let satcheckAt: string | null = null;
  /** #183: unsubscribe for the top-bar search -> map bridge. */
  let searchFocusOff: (() => void) | null = null;
  let stream: WatchStream | null = null;
  let feed: LiveFeed | null = null;
  let panelParts: {
    alertId: string;
    duration: HTMLElement;
    radius: HTMLElement | null;
    other: HTMLElement | null;
  } | null = null;
  let panelTicker: number | null = null;
  let clockTicker: number | null = null;
  let refreshing = false;
  let pendingRefresh = false;
  /** The selection to apply once the first snapshot exists. */
  let pending: { alertId: string | null; areaId: string | null } | null = {
    alertId: route.alertId,
    areaId: route.areaId ?? null,
  };
  /** Alerts the previous snapshot already had; rows outside it slide in. */
  let knownIds: Set<string> | null = null;
  let freshIds = new Set<string>();

  let radarPanel: RadarPanel | null = null;
  let radarToken = 0;
  let radarSelectedEncounter: number | null = null;

  const state: FilterState = { query: "", kinds: new Set(KIND_ORDER), areaId: null, windowH: 12 };
  let kindButtons: { kind: WatchAlertKind; button: HTMLButtonElement; count: HTMLElement }[] = [];
  let areaChips: { id: string; chip: HTMLButtonElement }[] = [];
  let pendingFit: { bbox: [number, number, number, number] | null } | null = null;
  let searchInput: HTMLInputElement | null = null;
  /** What the export button writes: exactly the rows on screen. */
  let exportRows: WatchAlert[] = [];

  // ---- clock: freshness chip, status bar, live duration -----------------------

  // Receivers' rate from Tiger (see fetchListeningRate); refreshed every 30 s.
  let receiverRate: number | null = null;
  const refreshRate = (): void => {
    fetchListeningRate()
      .then((rate) => {
        receiverRate = rate;
      })
      .catch(() => {
        receiverRate = null;
      });
  };
  refreshRate();
  const rateTimer = window.setInterval(refreshRate, 30_000);

  function paintChip(nowMs: number): void {
    if (data === null) {
      liveChip.textContent = "Live";
      return;
    }
    const delayed = !fixtureMode && recordingLagMs(data, nowMs) > DELAYED_MS;
    liveChip.classList.toggle("is-delayed", delayed);
    const parts: (string | HTMLElement)[] = [
      h("span", { class: "watch-live-dot", "aria-hidden": "true" }),
      "Live",
    ];
    if (receiverRate !== null) parts.push(` \u00b7 ${fmtInt(receiverRate)} position reports a minute`);
    parts.push(` · updated ${timeAgo(data.generated_at, nowMs)}`);
    if (delayed) parts.push(" · Recording delayed");
    liveChip.replaceChildren(...parts);
    liveChip.title = fixtureMode
      ? "Sample snapshot (opened with ?fixture)"
      : "Positions stream in live; events update whenever the recorder writes a snapshot";
  }

  function paintStatus(nowMs: number): void {
    statusClock.textContent = new Date(nowMs).toISOString().slice(11, 19) + "Z";
    if (data !== null) {
      statusSnap.textContent = `${fmtUtcZ(data.generated_at).slice(7)} · ${fmtInt(data.counts.ships)} ships`;
    }
    statusFeed.textContent =
      receiverRate === null ? "waiting" : `${fmtInt(receiverRate)} position reports a minute`;
  }

  function tick(): void {
    const now = Date.now();
    paintChip(now);
    paintStatus(now);
  }

  function stopPanelTicker(): void {
    if (panelTicker !== null) {
      window.clearInterval(panelTicker);
      panelTicker = null;
    }
  }

  /** Ongoing alerts tick: duration and radius read from now, not from the file. */
  function startPanelTicker(alert: WatchAlert): void {
    stopPanelTicker();
    if (alert.status !== "ongoing") return;
    panelTicker = window.setInterval(() => {
      if (destroyed || data === null || panelParts === null) return;
      const current = data.alerts.find((entry) => entry.id === panelParts?.alertId);
      if (current === undefined || current.status !== "ongoing") return;
      const now = Date.now();
      panelParts.duration.textContent = fmtDuration(durationMs(current, now));
      if (panelParts.radius !== null) {
        const atMs = reachTimeMs(current, now);
        panelParts.radius.textContent = `${fmtInt(Math.round(reachRadiusKm(current, atMs)))} km`;
      }
      if (panelParts.other !== null) {
        panelParts.other.textContent = `Other ships where it could have gone: ${fmtInt(otherShipsInReach(data, current, reachTimeMs(current, now)))}`;
      }
    }, 1000);
  }

  // ---- events rail ----------------------------------------------------------

  function markRows(): void {
    for (const row of document.querySelectorAll<HTMLButtonElement>(".watch-row")) {
      const id = row.dataset.alertId;
      if (id !== undefined && id === selectedId) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }
  }

  function setPressed(button: HTMLButtonElement, pressed: boolean): void {
    button.setAttribute("aria-pressed", pressed ? "true" : "false");
  }

  /** One alert row: used by the rail and by the area panel, so both do the same thing. */
  function alertRow(snapshot: WatchResponse, alert: WatchAlert, now: number): HTMLElement {
    const row = h(
      "button",
      {
        class: `watch-row is-${alert.kind}`,
        type: "button",
        "data-alert-id": alert.id,
        "aria-current": alert.id === selectedId ? "true" : null,
      },
      kindGlyph(alert.kind),
      h("span", { class: "watch-row-name" }, alertName(alert)),
      alert.status === "ongoing"
        ? h("span", { class: "watch-row-live", title: "Ongoing", "aria-label": "ongoing" })
        : null,
      alert.kind === "radar_unmatched"
        ? h(
            "time",
            { class: "watch-row-time", datetime: alert.t_start },
            h("span", { class: "watch-row-utc" }, fmtDayLabel(alert.t_start)),
            h("span", { class: "watch-row-rel" }, "radar day"),
          )
        : h(
            "time",
            { class: "watch-row-time", datetime: alert.t_start, title: "UTC (the Z means UTC time)" },
            h("span", { class: "watch-row-utc" }, fmtUtcZ(alert.t_start)),
            h("span", { class: "watch-row-rel" }, timeAgo(alert.t_start, now)),
          ),
      h("span", { class: "watch-row-tag" }, KIND_NAME[alert.kind] ?? alert.kind),
      h("span", { class: "watch-row-area" }, areaName(snapshot, alert.area_id)),
    );
    row.addEventListener("click", () => {
      location.hash = watchHref(alert.id);
    });
    return row;
  }

  /** Counts and control states from the current area + window scope. */
  function syncFilters(snapshot: WatchResponse): void {
    const counts = kindCounts(scopedAlerts(snapshot, state));
    for (const entry of kindButtons) {
      entry.count.textContent = fmtInt(counts.get(entry.kind) ?? 0);
      setPressed(entry.button, state.kinds.has(entry.kind));
    }
    for (const entry of areaChips) setPressed(entry.chip, entry.id === (state.areaId ?? ""));
    hiddenRoutine.textContent = `${fmtInt(snapshot.counts.hidden_routine)} routine events hidden`;
  }

  function renderRows(snapshot: WatchResponse): void {
    const now = Date.now();
    const alerts = visibleAlerts(snapshot, state);
    exportRows = alerts;
    eventsCount.textContent = fmtInt(alerts.length);
    rowsEl.replaceChildren();

    if (alerts.length === 0) {
      rowsEl.append(h("li", { class: "watch-empty" }, "No events match the filters"));
      return;
    }

    // Two groups, both already newest first: what is still going on, then the rest.
    const groups: [string, WatchAlert[]][] = [
      ["Happening now", alerts.filter((alert) => alert.status === "ongoing")],
      ["Earlier", alerts.filter((alert) => alert.status !== "ongoing")],
    ];
    for (const [title, items] of groups) {
      if (items.length === 0) continue;
      rowsEl.append(h("li", { class: "watch-group-head", role: "presentation" }, `${title} · ${items.length}`));
      for (const alert of items) {
        const item = h("li", { class: freshIds.has(alert.id) ? "is-new" : null }, alertRow(snapshot, alert, now));
        rowsEl.append(item);
      }
    }
  }

  /** The watched areas as rows: every area on the map can be opened from here too. */
  /**
   * One line of watched areas that have events in the window (issue #249). An area with nothing
   * to show is left out, and the whole block is hidden when no area has an event.
   */
  function renderAreas(snapshot: WatchResponse): void {
    const now = snapshotNow(snapshot);
    const active = snapshot.areas
      .filter((area) => area.kind === "watched")
      .map((area) => ({
        area,
        count: snapshot.alerts.filter((alert) => alert.area_id === area.id && inWindow(alert, now, 12)).length,
      }))
      .filter((entry) => entry.count > 0);
    areasCount.textContent = String(active.length);
    areasEl.hidden = active.length === 0;
    areasList.replaceChildren();
    if (active.length === 0) return;
    const line = h("li", { class: "watch-areas-line" });
    active.forEach(({ area, count }, index) => {
      if (index > 0) line.append(" · ");
      // The map may be showing the Baltic while Hormuz has events, so the Hormuz
      // row says where it goes instead of reading as a count for the current view (#259).
      if (area.id === HORMUZ_AREA_ID) {
        line.append(
          h(
            "a",
            { class: "watch-area-row", href: HORMUZ_HREF },
            h("span", { class: "watch-area-row-name" }, "Strait of Hormuz \u00b7 open the Hormuz screen \u2192"),
          ),
        );
        return;
      }
      const row = h(
        "button",
        {
          class: "watch-area-row",
          type: "button",
          "aria-current": area.id === selectedAreaId ? "true" : null,
          "data-area-id": area.id,
        },
        h("span", { class: "watch-area-row-name" }, area.name),
        " ",
        h("span", { class: "watch-mono watch-area-row-n is-hot" }, String(count)),
      );
      row.addEventListener("click", () => {
        location.hash = watchAreaHref(area.id);
      });
      line.append(row);
    });
    areasList.append(line);
  }

  /** One way to pick an area: filter the list and move the camera there (null = opening view). */
  function chooseArea(areaId: string | null): void {
    state.areaId = areaId;
    if (data !== null) {
      syncFilters(data);
      renderRows(data);
      const area = areaId === null ? undefined : data.areas.find((entry) => entry.id === areaId);
      const target = area?.bbox ?? null;
      if (selectedId === null && selectedAreaId === null) {
        map?.fitArea(target);
      } else {
        // Close what is open first, then steer the camera once the world view is back.
        pendingFit = { bbox: target };
        location.hash = "#/watch";
      }
    }
  }

  /** Build the filter row once per snapshot; each control edits `state` in place. */
  function buildFilters(snapshot: WatchResponse): void {
    const present = new Set(snapshot.alerts.map((alert) => alert.kind));
    kindButtons = [];
    const kinds = h("div", { class: "watch-filter-kinds", role: "group", "aria-label": "Event type" });
    for (const kind of KIND_ORDER) {
      if (!present.has(kind)) continue;
      const count = h("span", { class: "watch-kind-count" });
      const button = h(
        "button",
        {
          class: `watch-kind is-${kind}`,
          type: "button",
          title: KIND_TIP[kind] ?? `Show/hide ${KIND_NAME[kind].toLowerCase()} events`,
          "aria-pressed": state.kinds.has(kind) ? "true" : "false",
        },
        kindGlyph(kind),
        h("span", { class: "watch-kind-label" }, KIND_CHIP[kind]),
        count,
      );
      button.addEventListener("click", () => {
        if (state.kinds.has(kind)) state.kinds.delete(kind);
        else state.kinds.add(kind);
        if (data !== null) {
          syncFilters(data);
          renderRows(data);
        }
      });
      kinds.append(button);
      kindButtons.push({ kind, button, count });
    }

    // One segmented control: All, then each area with alerts in the list's window, busiest first.
    // The counts are fixed per snapshot (they ignore the kind and text filters).
    const now = snapshotNow(snapshot);
    const countIn = (areaId: string | null): number =>
      snapshot.alerts.filter(
        (alert) => (areaId === null || alert.area_id === areaId) && inWindow(alert, now, state.windowH),
      ).length;
    const areas = snapshot.areas
      .filter((area) => countIn(area.id) > 0)
      .sort((a, b) => countIn(b.id) - countIn(a.id));
    // Issue #249: the region control and the list's own search are cut; the top-bar search and
    // the map's area outlines do the same job. Only the kind filter stays in the list header.
    areaChips = [];
    searchInput = null;

    filtersEl.classList.remove("is-loading");
    filtersEl.replaceChildren(kinds);
  }

  function renderEvents(snapshot: WatchResponse): void {
    // Rows that were not in the previous snapshot slide in once, with a highlight.
    freshIds = new Set<string>();
    if (knownIds !== null) {
      for (const alert of snapshot.alerts) if (!knownIds.has(alert.id)) freshIds.add(alert.id);
    }
    knownIds = new Set(snapshot.alerts.map((alert) => alert.id));
    buildFilters(snapshot);
    syncFilters(snapshot);
    renderRows(snapshot);
    renderAreas(snapshot);
    freshIds = new Set<string>();
  }

  exportBtn.addEventListener("click", () => {
    if (data !== null) downloadCsv(exportRows, data);
  });

  back.addEventListener("click", () => {
    location.hash = "#/watch";
  });

  // ---- detail panel: one alert ---------------------------------------------

  /** A table of hulls; every row rings its hull on the map. */
  function contactTable(snapshot: WatchResponse, list: WatchContact[]): HTMLElement {
    const body = h("tbody");
    const table = h(
      "table",
      { class: "watch-nearby-table" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          h("th", { scope: "col" }, "Name"),
          h("th", { scope: "col", title: "The ship's radio ID (MMSI)" }, "MMSI"),
          h("th", { scope: "col", title: "Distance from the ship that switched off" }, "km"),
          h("th", { scope: "col" }, "Status"),
          h("th", { scope: "col" }, "Sanctioned"),
        ),
      ),
      body,
    );

    for (const contact of list) {
      const sanctioned = onSanctionsList(contact);
      const row = h(
        "tr",
        {
          class: sanctioned ? "is-sanctioned" : null,
          tabindex: "0",
          role: "button",
          "aria-label": `${contactName(contact)}, ${fmtDec(contact.min_dist_km, 1)} km`,
        },
        // The MMSI has its own column, so the name cell says "Unnamed" and the MMSI never wraps (#270).
        h("td", { class: "watch-nearby-name" }, contact.name?.trim() || "Unnamed"),
        h("td", { class: "watch-mono watch-mmsi", title: "The ship's radio ID (MMSI)" }, String(contact.mmsi)),
        h(
          "td",
          { class: "watch-mono watch-num", title: "Distance from the ship that switched off" },
          fmtDec(contact.min_dist_km, 1),
        ),
        h("td", null, contact.stopped ? "Stopped" : "Moving"),
        h("td", null, sanctionsListLabel(contact)),
      );
      const ring = (): void => {
        if (map === null) return;
        const ship = contactShip(snapshot, contact);
        map.focusContact({
          mmsi: contact.mmsi,
          name: contact.name,
          lat: ship?.lat ?? contact.lat,
          lon: ship?.lon ?? contact.lon,
          sanctioned,
        });
        for (const current of document.querySelectorAll<HTMLTableRowElement>(".watch-nearby-table tbody tr")) {
          current.removeAttribute("aria-current");
        }
        row.setAttribute("aria-current", "true");
        openDossier(contact.mmsi); // #182: a vessel row opens its dossier
      };
      row.addEventListener("click", ring);
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        ring();
      });
      body.append(row);
    }
    return table;
  }

  function section(title: string, count: number | null, ...content: HTMLElement[]): HTMLElement {
    return h(
      "section",
      { class: "watch-nearby" },
      h(
        "div",
        { class: "watch-section-head" },
        h("h3", null, title),
        count === null ? null : h("span", { class: "watch-count" }, String(count)),
      ),
      ...content,
    );
  }

  /** What the map draws as contacts: for a stopped ship, the ships nearest to it. */
  function mapAlertFor(snapshot: WatchResponse, alert: WatchAlert): WatchAlert {
    if (alert.kind === "spoof") return { ...alert, contacts: spoofContacts(snapshot, alert) };
    if (alert.kind !== "sanctioned_stop") return alert;
    return { ...alert, contacts: nearbyShips(snapshot, alert, 15, STOP_NEARBY_KM).shown };
  }

  /**
   * The faked-position event as one sentence (issue #249): how many ships jumped to the one point
   * and the speed range the jump needs. The map keeps each ship's line; the per-ship table went.
   */
  function spoofSection(_snapshot: WatchResponse, alert: WatchAlert): HTMLElement {
    const raw = alert.evidence["vessels"];
    const vessels = Array.isArray(raw)
      ? (raw as { mmsi: number; offset_km: number; jump_in_kn: number; jump_out_kn: number }[])
      : [];
    if (vessels.length === 0) {
      return section("Ships at the false point", 0, h("div", { class: "watch-none" }, "No ships nearby"));
    }
    const speeds = vessels.flatMap((vessel) => [vessel.jump_in_kn, vessel.jump_out_kn]);
    const low = Math.round(Math.min(...speeds));
    const high = Math.round(Math.max(...speeds));
    const noun = vessels.length === 1 ? "ship jumped" : "ships jumped";
    const sentence = `${vessels.length} ${noun} to one point; reaching it needs ${low}–${high} knots`;
    return section("Ships at the false point", vessels.length, h("p", { class: "watch-spoof-note" }, sentence));
  }

  function renderDetails(snapshot: WatchResponse, alert: WatchAlert): void {
    const now = Date.now();
    const atMs = reachTimeMs(alert, now);
    const duration = h("span", { class: "watch-mono" }, fmtDuration(durationMs(alert, now)));

    const radiusLabel =
      alert.kind === "dark" ? "Where it could have gone" : alert.kind === "sanctioned_stop" ? "Distance checked" : null;
    let radiusEl: HTMLElement | null = null;
    if (radiusLabel !== null) {
      radiusEl = h(
        "span",
        { class: "watch-mono", title: "Distance from the ship that switched off" },
        `${fmtInt(Math.round(reachRadiusKm(alert, atMs)))} km`,
      );
    }

    // Four rows (issue #249): MMSI, IMO and type belong in the vessel dossier, not the event.
    const fields = h(
      "dl",
      { class: "watch-fields" },
      ...field("Vessel", h("span", { class: "watch-vessel" }, alertName(alert))),
      ...field("Flag", flagOf(alert.mmsi)),
      ...field("Started", h("span", { class: "watch-time-cell" }, ...timeValue(alert.t_start, now))),
      ...field(
        "Last position",
        h("span", { class: "watch-mono" }, `${alert.last.lat.toFixed(4)}, ${alert.last.lon.toFixed(4)}`),
      ),
      // A dark alert's reach is already the first tile above: no second row for it.
      ...(alert.kind !== "dark" && radiusLabel !== null && reachRadiusKm(alert, atMs) >= 1
        ? field(radiusLabel, radiusEl as HTMLElement)
        : []),
    );

    // Top of every event panel: what happened, then the sanctions answer (issue #270).
    const sanctionsEl = h("p", { class: "watch-sanctions is-pending" }, "Checking the sanctions lists\u2026");
    loadSanctions(alert.mmsi, sanctionsEl);
    const parts: HTMLElement[] = [h("p", { class: "watch-headline" }, headlineOf(alert)), sanctionsEl];

    // The three numbers a dark alert is about, set large above the detail.
    if (alert.kind === "dark") {
      const met = alert.contacts.filter((contact) => contact.could_meet === true).length;
      const metN = reachCounts(alert).couldMeet ?? met;
      parts.push(
        h(
          "div",
          { class: "watch-figs" },
          figure(fmtDuration(durationMs(alert, now)), "Switched off for", "Time since its last broadcast"),
          figure(
            `${fmtInt(Math.round(reachRadiusKm(alert, atMs)))} km`,
            "Could have gone this far",
            "Furthest it could have sailed at top speed",
            "Distance from the ship that switched off",
          ),
          figure(fmtInt(metN), "Ships it could have met", "Ships it could have come alongside"),
        ),
      );
    }
    parts.push(fields);

    // #185: a dark alert's replay sits under the field table.
    if (alert.kind === "dark") {
      if (replay === null || replayAlertId !== alert.id) {
        replay?.destroy();
        replay = map === null ? null : mountReplay(alert, map);
        replayAlertId = alert.id;
      }
      if (replay !== null) parts.push(replay.element);

      // #268: the satellite radar result for this switch-off, right under the replay.
      satcheck?.destroy();
      satcheck = mountSatelliteCheck({
        alertId: alert.id,
        onPoints: (points, generatedAt) => {
          if (points === null) {
            satcheckAt = null;
            map?.showSatelliteCheck(null);
            return;
          }
          if (generatedAt !== null && generatedAt === satcheckAt) return;
          satcheckAt = generatedAt;
          map?.showSatelliteCheck(points);
        },
      });
      parts.push(satcheck.element);
    }

    // The receivers were off for part of this window: say so, and say it was not counted.
    const gap = overlappingGap(snapshot, alert, now);
    if (gap !== null) {
      parts.push(
        h(
          "div",
          { class: "watch-gapnote" },
          "Receivers offline ",
          h("span", { title: "UTC (the Z means UTC time)" }, `${gap.from}\u2013${gap.to}Z`),
          " \u2014 not counted",
        ),
      );
    }

    let otherEl: HTMLElement | null = null;

    if (alert.kind === "sanctioned_stop") {
      // A stopped ship has no reach story to tell: show who is nearest to it.
      const { shown, within } = nearbyShips(snapshot, alert, 15, STOP_NEARBY_KM);
      parts.push(
        section(
          `Ships within ${STOP_NEARBY_KM} km`,
          within,
          within === 0
            ? h("div", { class: "watch-none" }, `No other ship heard within ${STOP_NEARBY_KM} km`)
            : contactTable(snapshot, shown),
        ),
      );
    } else if (alert.kind === "spoof") {
      parts.push(spoofSection(snapshot, alert));
    } else if (alert.kind === "dark") {
      const sorted = sortedContacts(alert.contacts);
      const met = sorted.filter((contact) => contact.could_meet === true);
      const rest = sorted.filter((contact) => contact.could_meet !== true);
      // The same predicate as the table cell: the count is the rows that answer Yes,
      // not every row in the table (issue #259).
      const onList = met.filter(onSanctionsList).length;
      const counts = reachCounts(alert);
      const couldMeetN = counts.couldMeet ?? met.length;
      parts.push(
        section(
          `Ships it could have met \u00b7 ${fmtInt(couldMeetN)} (${fmtInt(onList)} on a sanctions list)`,
          null,
          met.length === 0
            ? h("div", { class: "watch-none" }, "No ship could have met it")
            : contactTable(snapshot, met),
        ),
      );
      // Ships that were inside the reach area but did not meet the test.
      const skip = new Set<number>([alert.mmsi, ...alert.contacts.map((contact) => contact.mmsi)]);
      const others: WatchContact[] = [];
      for (const ship of snapshot.ships) {
        if (skip.has(ship.mmsi)) continue;
        if ((ship as { valid?: boolean }).valid === false) continue;
        if (!insideReach(alert, ship.lat, ship.lon, atMs)) continue;
        others.push({
          mmsi: ship.mmsi,
          name: ship.name,
          watchlist: ship.watchlist,
          lat: ship.lat,
          lon: ship.lon,
          t: ship.t,
          min_dist_km: distanceKm(alert.last.lat, alert.last.lon, ship.lat, ship.lon),
          stopped: ship.sog <= 3,
          could_meet: false,
        });
      }
      others.sort((a, b) => a.min_dist_km - b.min_dist_km);
      const also = [...rest, ...others.slice(0, 40)];
      // Inside = every hull in reach (the build's count); ruled out = inside minus could-meet.
      const insideN = counts.inside ?? met.length + rest.length + others.length;
      const ruledOut = Math.max(0, insideN - couldMeetN);
      parts.push(
        h(
          "p",
          { class: "watch-reach-note" },
          `${fmtInt(insideN)} ships were inside the circle; their own tracks rule out ${fmtInt(ruledOut)}. Only ${fmtInt(couldMeetN)} could have been at the same place at the same time.`,
        ),
        h(
          "details",
          { class: "watch-also" },
          h("summary", null, "Show the ships ruled out"),
          also.length === 0 ? h("div", { class: "watch-none" }, "None") : contactTable(snapshot, also),
        ),
      );
    } else {
      const contacts = sortedContacts(alert.contacts);
      otherEl = h(
        "div",
        { class: "watch-other" },
        `Other ships where it could have gone: ${fmtInt(otherShipsInReach(snapshot, alert, atMs))}`,
      );
      parts.push(
        section(
          "Nearby vessels",
          contacts.length,
          contacts.length === 0 ? h("div", { class: "watch-none" }, "No ships where it could have gone") : contactTable(snapshot, contacts),
        ),
        otherEl,
      );
    }

    detailBody.replaceChildren(...parts);

    panelParts = {
      alertId: alert.id,
      duration,
      radius: radiusEl,
      other: otherEl,
    };
  }

  // ---- detail panel: an area ------------------------------------------------

  function renderAreaPanel(snapshot: WatchResponse, areaId: string): void {
    const area = snapshot.areas.find((entry) => entry.id === areaId);
    if (area === undefined) return;
    const now = Date.now();
    const snapNow = snapshotNow(snapshot);
    const [w, s, e, n] = area.bbox;
    const heard = snapshot.ships.filter(
      (ship) => ship.lon >= w && ship.lon <= e && ship.lat >= s && ship.lat <= n,
    ).length;
    const inArea = sortedAlerts(
      snapshot.alerts.filter((alert) => alert.area_id === areaId && inWindow(alert, snapNow, 12)),
    );

    const fields = h(
      "dl",
      { class: "watch-fields" },
      ...field("Area", h("span", { class: "watch-vessel" }, area.name)),
      ...field("Kind", area.kind === "watched" ? "Watched" : "Listening"),
      ...field("Ships heard", h("span", { class: "watch-mono" }, fmtInt(heard))),
      ...field("Events, 12 h", h("span", { class: "watch-mono" }, String(inArea.length))),
      ...field(
        "Box",
        h("span", { class: "watch-mono" }, `${s.toFixed(2)}–${n.toFixed(2)}N, ${w.toFixed(2)}–${e.toFixed(2)}E`),
      ),
    );

    const list =
      inArea.length === 0
        ? h(
            "div",
            { class: "watch-none watch-area-empty" },
            `No suspicious activity here in the last 12 h · ${heard} ship${heard === 1 ? "" : "s"} heard`,
          )
        : h(
            "ol",
            { class: "watch-rows is-inline" },
            ...inArea.map((alert) => h("li", null, alertRow(snapshot, alert, now))),
          );

    detailBody.replaceChildren(fields, section("Events", inArea.length, list));
    panelParts = null;
  }

  // ---- detail panel: the Hormuz radar picture ------------------------------

  function setAttrib(gfw: boolean): void {
    const next = watchAttribution(gfw);
    attrib.replaceWith(next);
    attrib = next;
  }

  function disposeRadar(): void {
    radarToken += 1;
    radarPanel?.destroy();
    radarPanel = null;
    radarSelectedEncounter = null;
  }

  // The radar story lives on the Hormuz screen (finding, 2-year chart, today's satellite
  // pass). Kassem: the old per-day bars and replay here were noise; send people there.
  function showRadar(_alert: WatchAlert | null): void {
    disposeRadar();
    window.location.hash = HORMUZ_HREF;
  }

  let radarMarkerHandler: ((kind: "encounter" | "hotspot", index: number) => void) | null = null;


  // ---- selection ------------------------------------------------------------

  function dropReplay(): void {
    replay?.destroy();
    replay = null;
    replayAlertId = null;
  }

  function applySelection(options: { keepCamera: boolean }): void {
    if (data === null) return;
    const alert =
      selectedId === null ? null : (data.alerts.find((entry) => entry.id === selectedId) ?? null);
    const area =
      selectedAreaId === null ? null : (data.areas.find((entry) => entry.id === selectedAreaId) ?? null);

    const radarMode =
      (alert !== null && alert.kind === "radar_unmatched") ||
      (alert === null && area !== null && area.id === HORMUZ_AREA_ID);
    const areaMode = alert === null && area !== null && !radarMode;
    const open = alert !== null || area !== null;
    if (alert === null || alert.kind !== "dark") dropReplay();

    // #268: the satellite check belongs to one dark alert. A different selection,
    // or none, clears its dots; the same alert keeps them through a refresh.
    const satcheckFor = alert !== null && alert.kind === "dark" ? alert.id : null;
    if (satcheckFor !== satcheckAlertId) {
      satcheck?.destroy();
      satcheck = null;
      satcheckAt = null;
      satcheckAlertId = satcheckFor;
      map?.showSatelliteCheck(null);
    }

    main.classList.toggle("is-focus", open);
    markRows();
    for (const row of areasList.querySelectorAll<HTMLButtonElement>(".watch-area-row")) {
      if (row.dataset.areaId === area?.id && alert === null) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }

    if (!open) {
      detailBody.replaceChildren(detailEmpty);
      panelParts = null;
      stopPanelTicker();
      disposeRadar();
      setAttrib(false);
      if (appliedKey !== null) closeDossier(); // leaving a selection drops its dossier overlay
      appliedKey = null;
      map?.showWorld();
      if (pendingFit !== null) {
        map?.fitArea(pendingFit.bbox);
        pendingFit = null;
      }
      return;
    }

    const key = radarMode
      ? `radar:${alert?.id ?? "area"}`
      : areaMode
        ? `area:${area?.id}`
        : `alert:${alert?.id}`;
    const same = appliedKey === key;
    if (!same) closeDossier(); // #182: a new selection replaces the dossier overlay

    if (radarMode) {
      detailsTitle.textContent = "Radar picture";
      stopPanelTicker();
      panelParts = null;
      // A snapshot refresh must not restart the time-lapse the viewer is watching.
      if (!(same && options.keepCamera)) {
        details.scrollTop = 0;
        showRadar(alert);
      }
    } else if (areaMode && area !== null) {
      detailsTitle.textContent = "Area";
      stopPanelTicker();
      disposeRadar();
      setAttrib(false);
      map?.setRadar(null);
      renderAreaPanel(data, area.id);
      if (!(same && options.keepCamera)) {
        details.scrollTop = 0;
        map?.showRegion({ bounds: area.bbox, areaId: area.id, alertId: null });
      }
    } else if (alert !== null) {
      detailsTitle.textContent = "Event details";
      disposeRadar();
      setAttrib(false);
      map?.setRadar(null);
      if (!same) details.scrollTop = 0;
      renderDetails(data, alert);
      map?.focusAlert(mapAlertFor(data, alert), {
        animate: !options.keepCamera,
        moveCamera: !(options.keepCamera && same),
      });
      startPanelTicker(alert);
    }
    appliedKey = key;
    updateMoreCue();
  }

  function select(alertId: string | null, areaId: string | null = null): void {
    if (destroyed) return;
    selectedId = alertId;
    selectedAreaId = alertId === null ? areaId : null;
    if (data === null) {
      pending = { alertId, areaId };
      return;
    }
    pending = null;
    applySelection({ keepCamera: false });
  }

  // ---- keyboard --------------------------------------------------------------

  function onKeydown(event: KeyboardEvent): void {
    if (destroyed || document.body.classList.contains("is-presenting")) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target as HTMLElement | null;
    const typing =
      target !== null && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA");

    if (event.key === "Escape") {
      if (selectedId === null && selectedAreaId === null) return;
      event.preventDefault();
      location.hash = "#/watch";
      return;
    }
    if (typing) return;

    if ((event.key === "j" || event.key === "k" || event.key === "J" || event.key === "K") && data !== null) {
      const list = visibleAlerts(data, state);
      if (list.length === 0) return;
      event.preventDefault();
      const at = list.findIndex((entry) => entry.id === selectedId);
      const step = event.key.toLowerCase() === "j" ? 1 : -1;
      const next = at < 0 ? (step === 1 ? 0 : list.length - 1) : Math.min(list.length - 1, Math.max(0, at + step));
      const target = list[next];
      if (target !== undefined) {
        location.hash = watchHref(target.id);
        document
          .querySelector<HTMLElement>(`.watch-row[data-alert-id="${CSS.escape(target.id)}"]`)
          ?.scrollIntoView({ block: "nearest" });
      }
    }
  }
  document.addEventListener("keydown", onKeydown);

  // ---- data -----------------------------------------------------------------

  function build(snapshot: WatchResponse): void {
    data = snapshot;

    map = mountWatchMap({
      container: mapDiv,
      onSelectAlert: (id) => {
        location.hash = watchHref(id);
      },
      onSelectArea: (id) => {
        location.hash = watchAreaHref(id);
      },
      onSelectMarker: (kind, index) => radarMarkerHandler?.(kind, index),
      onSelectVessel: (mmsi) => openDossier(mmsi), // #182: any ship dot opens its dossier
      opening: openingBox(snapshot),
    });
    setDossierTrackRenderer((track) => map?.showDossierTrack(track));
    searchFocusOff = onVesselFocus((vessel) => {
      map?.flyToVessel(vessel.lat, vessel.lon); // #183
      openDossier(vessel.mmsi); // search hit = fly + dossier
    });
    map.setData(snapshot);
    renderEvents(snapshot);
    paintChip(Date.now());

    // Positions stream in from the recorder: the dots glide and pulse as they arrive.
    if (!fixtureMode) {
      feed = openLiveFeed(LIVE_REGIONS, (items) => map?.applyLive(items));
    }
    paintStatus(Date.now());

    let initial = pending ?? { alertId: selectedId, areaId: selectedAreaId };
    // A cold open with nothing running shows one story, not an empty map: the lens picks it.
    if (initial.alertId === null && initial.areaId === null && !lensUsed) {
      const lens = pickLens(snapshot);
      if (lens !== null) {
        lensUsed = true;
        initial = { alertId: lens.id, areaId: null };
        history.replaceState(null, "", watchHref(lens.id));
      }
    }
    selectedId = initial.alertId;
    selectedAreaId = initial.areaId;
    if (selectedId !== null && !snapshot.alerts.some((alert) => alert.id === selectedId)) selectedId = null;
    if (selectedAreaId !== null && !snapshot.areas.some((area) => area.id === selectedAreaId)) {
      selectedAreaId = null;
    }
    pending = null;
    appliedKey = null;
    applySelection({ keepCamera: false });
  }

  async function load(): Promise<void> {
    if (refreshing) {
      pendingRefresh = true;
      return;
    }
    refreshing = true;
    try {
      let response: WatchResponse;
      if (fixtureMode) {
        const res = await fetch(watchFixtureUrl(), { headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error(`fixture HTTP ${res.status}`);
        response = (await res.json()) as WatchResponse;
      } else {
        response = await fetchWatch(abort.signal);
      }
      if (destroyed) return;

      if (data === null) {
        build(response);
        clockTicker = window.setInterval(tick, 1000);
        if (!fixtureMode) {
          stream = watchStream(() => {
            void load();
          });
        }
      } else {
        data = response;
        map?.setData(response);
        renderEvents(response);
        tick();
        applySelection({ keepCamera: true });
      }
    } catch (err) {
      if (destroyed || abort.signal.aborted) return;
      if (data === null) {
        console.error("watch screen failed", err);
        main.replaceChildren(
          h(
            "div",
            { class: "watch-error" },
            h(
              "div",
              { class: "banner is-error" },
              "The watch snapshot is not available. Start the API with DARKFLEET_DATA=data/snapshot uv run uvicorn darkfleet.api.app:app --port 8000.",
            ),
          ),
        );
      }
    } finally {
      refreshing = false;
      if (pendingRefresh && !destroyed) {
        pendingRefresh = false;
        void load();
      }
    }
  }

  void load();

  // Patrol sightings (#220): a 10 s poll; the group stays hidden while the unit has none.
  async function refreshSightings(): Promise<void> {
    const payload = await fetchSightings(abort.signal);
    if (destroyed) return;
    const items = payload?.feed.filter((item) => item.lat !== null && item.lon !== null) ?? [];
    sightingsEl.replaceChildren();
    sightingsEl.hidden = items.length === 0 || payload === null;
    if (payload === null || items.length === 0) return;
    const names = unitNames(payload);
    sightingsEl.append(h("li", { class: "watch-group-head", role: "presentation" }, `Patrol sightings · ${items.length}`));
    for (const run of collapseSightings(items, names)) {
      sightingsEl.append(
        h(
          "li",
          null,
          sightingRunRow(run, (picked) => {
            if (picked.lat !== null && picked.lon !== null) map?.flyToVessel(picked.lat, picked.lon);
          }),
        ),
      );
    }
  }
  void refreshSightings();
  sightingsTimer = window.setInterval(() => void refreshSightings(), SIGHTINGS_POLL_MS);

  return {
    select,
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      document.removeEventListener("keydown", onKeydown);
      stopPanelTicker();
      disposeRadar();
      if (clockTicker !== null) {
        window.clearInterval(clockTicker);
        clockTicker = null;
      }
      if (sightingsTimer !== null) {
        window.clearInterval(sightingsTimer);
        sightingsTimer = null;
      }
      window.clearInterval(rateTimer);
      stream?.close();
      stream = null;
      feed?.close();
      feed = null;
      abort.abort();
      dropReplay();
      satcheck?.destroy();
      satcheck = null;
      searchFocusOff?.();
      searchFocusOff = null;
      setDossierTrackRenderer(null);
      closeDossier();
      map?.destroy();
      map = null;
    },
  };
}
