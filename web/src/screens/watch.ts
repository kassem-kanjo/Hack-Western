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
  fetchHormuzHistory,
  fetchHormuzUnseen,
  fetchWatch,
  watchFixtureUrl,
  watchStream,
} from "../api";
import type { HormuzHistoryFile, HormuzUnseenFile, WatchArea, WatchRecording } from "../api";
import { mountReplay, type ReplayView } from "../components/AlertReplay";
import { h } from "../components/dom";
import { onVesselFocus } from "../components/SearchBox";
import { topBar } from "../components/TopBar";
import { closeDossier, openDossier, setDossierTrackRenderer } from "../components/VesselDossier";
import { fmtUtc } from "../format";
import { alertBreakdown, fetchAreaStats, sparkline, totalAlerts } from "../model/areaStats";
import { timeAgo } from "../model/liveFacts";
import { LIVE_REGIONS, openLiveFeed, type LiveFeed } from "../model/liveFeed";
import {
  buildRadarPanel,
  GFW_URL,
  latestWellCoveredDay,
  radarBounds,
  radarMapView,
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
const KIND_NAME: Record<WatchAlertKind, string> = {
  dark: "Dark",
  sanctioned_stop: "Sanctioned stop",
  spoof: "Spoof",
  meeting: "Meeting",
  jump: "Jump",
  radar_unmatched: "Radar",
};

/** Toggle order: what Kassem asked for first, then the remaining contract kind. */
const KIND_ORDER: WatchAlertKind[] = [
  "dark",
  "sanctioned_stop",
  "spoof",
  "meeting",
  "jump",
  "radar_unmatched",
];

/** A vessel name, or the contract's honest placeholder. */
function alertName(alert: WatchAlert): string {
  // A radar alert is fleet-level: it names no hull, so the row says what it saw.
  if (alert.kind === "radar_unmatched") {
    const n = alert.evidence["unmatched"];
    return typeof n === "number" ? `Radar \u00b7 ${n} not broadcasting` : shortLabel(alert.label);
  }
  const trimmed = alert.name?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : `Unnamed \u00b7 MMSI ${alert.mmsi}`;
}

function contactName(contact: WatchContact): string {
  const trimmed = contact.name?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : `Unnamed \u00b7 MMSI ${contact.mmsi}`;
}

/** Backend labels are already short; this makes the six-word rule structural. */
function shortLabel(label: string): string {
  const words = label.trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 6).join(" ");
}

function areaName(data: WatchResponse, areaId: string | null): string {
  if (areaId === null) return "No area";
  return data.areas.find((area) => area.id === areaId)?.name ?? areaId;
}

/** Severity, then most recent: the list's one order. */
function sortedAlerts(alerts: WatchAlert[]): WatchAlert[] {
  return [...alerts].sort((a, b) => {
    if (b.severity !== a.severity) return b.severity - a.severity;
    const aStart = Date.parse(a.t_start);
    const bStart = Date.parse(b.t_start);
    if (Number.isFinite(aStart) && Number.isFinite(bStart) && aStart !== bStart) {
      return bStart - aStart;
    }
    return 0;
  });
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
    const aS = a.watchlist === "sanctioned" ? 1 : 0;
    const bS = b.watchlist === "sanctioned" ? 1 : 0;
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

/** MMSI -> flag state via its MID, or an em dash when the MID is not known. */
function flagOf(mmsi: number): string {
  if (!Number.isFinite(mmsi)) return "\u2014";
  const mid = Math.floor(mmsi / 1_000_000);
  return MID_COUNTRY[mid] ?? "\u2014";
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
  if (cls === undefined || cls.trim().length === 0) return "\u2014";
  return cls.charAt(0).toUpperCase() + cls.slice(1);
}

function watchlistLabel(contact: WatchContact): string {
  if (contact.watchlist === "sanctioned") return "Sanctioned";
  if (contact.watchlist === "shadow_fleet") return "Shadow fleet";
  return "\u2014";
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
  return sortedAlerts(
    scopedAlerts(data, state).filter((alert) => state.kinds.has(alert.kind)),
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
    h("time", { class: "watch-mono", datetime: iso }, fmtUtcZ(iso)),
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

/** Ships heard inside one area's box. */
function shipsInArea(snapshot: WatchResponse, area: WatchArea): number {
  const [w, s, e, n] = area.bbox;
  let count = 0;
  for (const ship of snapshot.ships) {
    if ((ship as { valid?: boolean }).valid === false) continue;
    if (ship.lon >= w && ship.lon <= e && ship.lat >= s && ship.lat <= n) count += 1;
  }
  return count;
}

/** Areas too quiet to offer: fewer than 20 ships heard, no alert and not watched. */
const MIN_SHIPS_FOR_CHIP = 20;

function offeredAreas(snapshot: WatchResponse): { area: WatchArea; ships: number }[] {
  const withAlerts = new Set(snapshot.alerts.map((alert) => alert.area_id));
  return snapshot.areas
    .map((area) => ({ area, ships: shipsInArea(snapshot, area) }))
    .filter(
      (entry) =>
        entry.ships >= MIN_SHIPS_FOR_CHIP || entry.area.kind === "watched" || withAlerts.has(entry.area.id),
    )
    .sort((a, b) => b.ships - a.ships);
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

/** Hulls heard recently, nearest first with watchlisted ones ahead; the list for a stopped ship. */
function nearbyShips(snapshot: WatchResponse, alert: WatchAlert, limit: number): WatchContact[] {
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
  // Sanctioned first, then the rest, but only those inside a radius a viewer can reason about.
  return out.slice(0, limit);
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
  const events = h(
    "aside",
    { class: "watch-events" },
    h("div", { class: "watch-events-head" }, h("h2", null, "Events"), eventsCount),
    filtersEl,
    rowsEl,
    areasEl,
    footEl,
  );

  const detailBody = h("div", { class: "watch-details-body" });
  const detailsTitle = h("span", { class: "watch-details-title" }, "Event details");
  const back = h(
    "button",
    { class: "watch-back", type: "button", "aria-label": "Back to events" },
    "← Events",
  );
  const details = h(
    "aside",
    { class: "watch-details", hidden: true },
    h("div", { class: "watch-details-head" }, back, detailsTitle),
    detailBody,
  );

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
    h("span", { class: "watch-status-item" }, h("span", { class: "watch-status-k" }, "Snapshot"), statusSnap),
    h("span", { class: "watch-status-item" }, h("span", { class: "watch-status-k" }, "AIS feed"), statusFeed),
    h("span", { class: "watch-status-item is-keys" }, keyHint("J"), keyHint("K"), "move", keyHint("Esc"), "back", keyHint("/"), "find ship"),
    h("span", { class: "watch-status-item is-clock" }, h("span", { class: "watch-status-k" }, "UTC"), statusClock),
  );

  function keyHint(key: string): HTMLElement {
    return h("kbd", { class: "watch-kbd" }, key);
  }

  const main = h("main", { class: "main watch-main" }, stage, events, details, status);

  app.replaceChildren(
    topBar({ active: "watch", mode: "LIVE", generatedAt: null, caseHref: null, demoHref: null }),
    main,
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
  let radarData: Promise<{ unseen: HormuzUnseenFile; history: HormuzHistoryFile }> | null = null;
  let radarToken = 0;
  let radarSelectedEncounter: number | null = null;

  const state: FilterState = { query: "", kinds: new Set(KIND_ORDER), areaId: null, windowH: 12 };
  let kindButtons: { kind: WatchAlertKind; button: HTMLButtonElement; count: HTMLElement }[] = [];
  let areaSelect: HTMLSelectElement | null = null;
  let areaChips: { id: string; chip: HTMLButtonElement }[] = [];
  let pendingFit: { bbox: [number, number, number, number] | null } | null = null;
  let searchInput: HTMLInputElement | null = null;
  let windowButtons: { hours: WindowHours; button: HTMLButtonElement }[] = [];
  /** What the export button writes: exactly the rows on screen. */
  let exportRows: WatchAlert[] = [];

  // ---- clock: freshness chip, status bar, live duration -----------------------

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
    if (feed !== null && feed.connected() && feed.perMinute() > 0) {
      parts.push(` \u00b7 ${feed.perMinute()} msgs/min`);
    }
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
      statusSnap.textContent = `${fmtUtcZ(data.generated_at).slice(7)} · ${data.counts.ships} ships`;
    }
    statusFeed.textContent =
      feed !== null && feed.connected() ? `${feed.perMinute()} msgs/min` : "waiting";
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
        panelParts.radius.textContent = `${Math.round(reachRadiusKm(current, atMs))} km`;
      }
      if (panelParts.other !== null) {
        panelParts.other.textContent = `Other ships in reach: ${otherShipsInReach(data, current, reachTimeMs(current, now))}`;
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
      h("span", { class: "watch-square", "aria-hidden": "true" }),
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
            { class: "watch-row-time", datetime: alert.t_start },
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
      entry.count.textContent = String(counts.get(entry.kind) ?? 0);
      setPressed(entry.button, state.kinds.has(entry.kind));
    }
    if (areaSelect !== null) areaSelect.value = state.areaId ?? "";
    for (const entry of areaChips) setPressed(entry.chip, entry.id === state.areaId);
    for (const entry of windowButtons) setPressed(entry.button, entry.hours === state.windowH);
    hiddenRoutine.textContent = `${snapshot.counts.hidden_routine} routine events hidden`;
  }

  function renderRows(snapshot: WatchResponse): void {
    const now = Date.now();
    const alerts = visibleAlerts(snapshot, state);
    exportRows = alerts;
    eventsCount.textContent = String(alerts.length);
    rowsEl.replaceChildren();

    if (alerts.length === 0) {
      rowsEl.append(h("li", { class: "watch-empty" }, "No events match the filters"));
      return;
    }

    for (const alert of alerts) {
      const item = h("li", { class: freshIds.has(alert.id) ? "is-new" : null }, alertRow(snapshot, alert, now));
      rowsEl.append(item);
    }
  }

  /** The watched areas as rows: every area on the map can be opened from here too. */
  function renderAreas(snapshot: WatchResponse): void {
    const watched = snapshot.areas.filter((area) => area.kind === "watched");
    areasCount.textContent = String(watched.length);
    areasList.replaceChildren();
    const now = snapshotNow(snapshot);
    for (const area of watched) {
      const count = snapshot.alerts.filter(
        (alert) => alert.area_id === area.id && inWindow(alert, now, 12),
      ).length;
      const row = h(
        "button",
        {
          class: "watch-area-row",
          type: "button",
          "aria-current": area.id === selectedAreaId ? "true" : null,
          "data-area-id": area.id,
        },
        h("span", { class: "watch-area-row-name" }, area.name),
        h("span", { class: count > 0 ? "watch-mono watch-area-row-n is-hot" : "watch-mono watch-area-row-n" }, String(count)),
      );
      row.addEventListener("click", () => {
        location.hash = watchAreaHref(area.id);
      });
      areasList.append(h("li", null, row));
    }
  }

  /** One way to pick an area: filter the list and move the camera there (null = opening view). */
  /** #186: add alerts (12 h) and a ship-count sparkline to each chip; plain chips if the API is down. */
  async function enrichChips(): Promise<void> {
    try {
      const stats = await fetchAreaStats(abort.signal);
      const byId = new Map(stats.map((stat) => [stat.id, stat]));
      for (const { id, chip } of areaChips) {
        const stat = byId.get(id);
        if (stat === undefined) continue;
        chip.querySelectorAll(".watch-chip-alerts, .watch-chip-spark").forEach((node) => node.remove());
        const alerts = totalAlerts(stat);
        if (alerts > 0) {
          chip.append(h("span", { class: "watch-mono watch-chip-alerts" }, `${alerts}\u26a0`));
        }
        chip.append(sparkline(stat.sparkline));
        const breakdown = alertBreakdown(stat);
        chip.title = `${stat.name}: ${stat.ships_now} ships now, ${alerts} alerts in 12 h${breakdown ? ` (${breakdown})` : ""}`;
      }
    } catch {
      // Chips stay as they are.
    }
  }

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
          "aria-pressed": state.kinds.has(kind) ? "true" : "false",
        },
        h("span", { class: "watch-square", "aria-hidden": "true" }),
        h("span", { class: "watch-kind-label" }, KIND_NAME[kind]),
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

    const offered = offeredAreas(snapshot);
    const select = h("select", { class: "watch-select", "aria-label": "Area" });
    select.append(h("option", { value: "" }, "All areas"));
    const watched = offered.filter((entry) => entry.area.kind === "watched").map((entry) => entry.area);
    const listening = offered.filter((entry) => entry.area.kind === "listening").map((entry) => entry.area);
    if (watched.length > 0) {
      const group = h("optgroup", { label: "Watched" });
      for (const area of watched) group.append(h("option", { value: area.id }, area.name));
      select.append(group);
    }
    if (listening.length > 0) {
      const group = h("optgroup", { label: "Listening" });
      for (const area of listening) group.append(h("option", { value: area.id }, area.name));
      select.append(group);
    }
    select.value = state.areaId ?? "";
    select.addEventListener("change", () => chooseArea(select.value === "" ? null : select.value));
    areaSelect = select;

    // Chips: the listening areas with ships in them, busiest first, each with its count.
    areaChips = [];
    const chips = h("div", { class: "watch-chips", role: "group", "aria-label": "Listening area" });
    for (const entry of offered.filter((item) => item.area.kind === "listening")) {
      const chip = h(
        "button",
        { class: "watch-chip", type: "button", "aria-pressed": "false" },
        h("span", null, entry.area.name),
        h("span", { class: "watch-mono" }, entry.ships.toLocaleString("en-US")),
      );
      chip.addEventListener("click", () => chooseArea(state.areaId === entry.area.id ? null : entry.area.id));
      chips.append(chip);
      areaChips.push({ id: entry.area.id, chip });
    }
    void enrichChips();

    windowButtons = [];
    const windowGroup = h("div", { class: "watch-window", role: "group", "aria-label": "Window" });
    for (const hours of [6, 12] as WindowHours[]) {
      const button = h(
        "button",
        {
          class: "watch-window-btn",
          type: "button",
          "aria-pressed": state.windowH === hours ? "true" : "false",
        },
        `${hours} h`,
      );
      button.addEventListener("click", () => {
        state.windowH = hours;
        if (data !== null) {
          syncFilters(data);
          renderRows(data);
        }
      });
      windowGroup.append(button);
      windowButtons.push({ hours, button });
    }

    const search = h("input", {
      class: "watch-search",
      type: "search",
      placeholder: "Filter this list",
      "aria-label": "Filter events by name or MMSI",
      autocomplete: "off",
      spellcheck: "false",
    });
    search.value = state.query;
    search.addEventListener("input", () => {
      state.query = search.value.trim().toLowerCase();
      if (data !== null) {
        syncFilters(data);
        renderRows(data);
      }
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        search.blur();
      }
    });
    searchInput = search;

    filtersEl.classList.remove("is-loading");
    // The 6 h / 12 h switch only appears when it would change the list.
    const matters = snapshot.alerts.some((alert) => {
      if (alert.kind === "radar_unmatched") return false;
      const age = snapshotNow(snapshot) - Date.parse(alert.t_start);
      return age > 6 * 3_600_000 && age <= 12 * 3_600_000;
    });
    if (!matters) state.windowH = 12;
    filtersEl.replaceChildren(
      search,
      chips,
      kinds,
      h("div", { class: "watch-filter-row" }, select, matters ? windowGroup : null),
    );
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
          h("th", { scope: "col" }, "MMSI"),
          h("th", { scope: "col" }, "km"),
          h("th", { scope: "col" }, "Status"),
          h("th", { scope: "col" }, "List"),
        ),
      ),
      body,
    );

    for (const contact of list) {
      const sanctioned = contact.watchlist === "sanctioned";
      const row = h(
        "tr",
        {
          class: sanctioned ? "is-sanctioned" : null,
          tabindex: "0",
          role: "button",
          "aria-label": `${contactName(contact)}, ${contact.min_dist_km.toFixed(1)} km`,
        },
        h("td", { class: "watch-nearby-name" }, contactName(contact)),
        h("td", { class: "watch-mono" }, String(contact.mmsi)),
        h("td", { class: "watch-mono watch-num" }, contact.min_dist_km.toFixed(1)),
        h("td", null, contact.stopped ? "Stopped" : "Moving"),
        h("td", null, watchlistLabel(contact)),
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
    return { ...alert, contacts: nearbyShips(snapshot, alert, 15) };
  }

  /** One row per ship that reported the false point, with its offset and the speed the jump implies. */
  function spoofSection(snapshot: WatchResponse, alert: WatchAlert): HTMLElement {
    const raw = alert.evidence["vessels"];
    const vessels = Array.isArray(raw)
      ? (raw as { mmsi: number; offset_km: number; jump_in_kn: number; jump_out_kn: number }[])
      : [];
    const located = new Map(spoofContacts(snapshot, alert).map((contact) => [contact.mmsi, contact]));
    const byMmsi = new Map(snapshot.ships.map((ship) => [ship.mmsi, ship]));
    const body = h("tbody");
    for (const vessel of vessels) {
      const ship = byMmsi.get(vessel.mmsi);
      const label = ship?.name?.trim() ? (ship.name as string) : `MMSI ${vessel.mmsi}`;
      const row = h(
        "tr",
        { tabindex: "0", role: "button", title: label },
        h("td", { class: "watch-nearby-name" }, label),
        h("td", { class: "watch-mono watch-num" }, vessel.offset_km.toFixed(1)),
        h("td", { class: "watch-mono watch-num" }, `${Math.round(vessel.jump_in_kn)}`),
        h("td", { class: "watch-mono watch-num" }, `${Math.round(vessel.jump_out_kn)}`),
      );
      const ring = (): void => {
        const at = located.get(vessel.mmsi);
        if (map === null || at === undefined) return;
        map.focusContact({ mmsi: at.mmsi, name: at.name, lat: at.lat, lon: at.lon, sanctioned: false });
        for (const current of document.querySelectorAll<HTMLTableRowElement>(".watch-spoof-table tbody tr")) {
          current.removeAttribute("aria-current");
        }
        row.setAttribute("aria-current", "true");
      };
      row.addEventListener("click", () => {
        ring();
        openDossier(vessel.mmsi); // #182: a spoof-table row opens its dossier
      });
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        ring();
        openDossier(vessel.mmsi);
      });
      body.append(row);
    }
    const table = h(
      "table",
      { class: "watch-nearby-table watch-spoof-table" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          h("th", { scope: "col" }, "Ship"),
          h("th", { scope: "col" }, "Off (km)"),
          h("th", { scope: "col" }, "In (kn)"),
          h("th", { scope: "col" }, "Out (kn)"),
        ),
      ),
      body,
    );
    return section(
      "Ships at the false point",
      vessels.length,
      vessels.length === 0 ? h("div", { class: "watch-none" }, "No ships listed") : table,
    );
  }

  function renderDetails(snapshot: WatchResponse, alert: WatchAlert): void {
    const now = Date.now();
    const atMs = reachTimeMs(alert, now);
    const duration = h("span", { class: "watch-mono" }, fmtDuration(durationMs(alert, now)));

    const radiusLabel =
      alert.kind === "dark" ? "Reach radius" : alert.kind === "sanctioned_stop" ? "Distance checked" : null;
    let radiusEl: HTMLElement | null = null;
    if (radiusLabel !== null) {
      radiusEl = h("span", { class: "watch-mono" }, `${Math.round(reachRadiusKm(alert, atMs))} km`);
    }

    const imo = imoOf(alert);
    const fields = h(
      "dl",
      { class: "watch-fields" },
      ...field("Vessel", h("span", { class: "watch-vessel" }, alertName(alert))),
      ...field("MMSI", h("span", { class: "watch-mono" }, String(alert.mmsi))),
      ...(imo !== null ? field("IMO", h("span", { class: "watch-mono" }, imo)) : []),
      ...field("Type", shipType(snapshot, alert)),
      ...field("Flag", flagOf(alert.mmsi)),
      ...field("Event", shortLabel(alert.label)),
      ...field("Area", areaName(snapshot, alert.area_id)),
      ...field("Started", h("span", { class: "watch-time-cell" }, ...timeValue(alert.t_start, now))),
      ...field(
        "Ended",
        alert.t_end === null
          ? h(
              "span",
              { class: "watch-ongoing" },
              h("span", { class: "watch-row-live", "aria-hidden": "true" }),
              "ongoing",
            )
          : h("span", { class: "watch-time-cell" }, ...timeValue(alert.t_end, now)),
      ),
      ...field("Duration", duration),
      ...field(
        "Last position",
        h("span", { class: "watch-mono" }, `${alert.last.lat.toFixed(4)}, ${alert.last.lon.toFixed(4)}`),
      ),
      ...(radiusLabel !== null && reachRadiusKm(alert, atMs) >= 1
        ? field(radiusLabel, radiusEl as HTMLElement)
        : []),
    );

    const parts: HTMLElement[] = [fields];

    // #185: a dark alert's replay sits under the field table.
    if (alert.kind === "dark") {
      if (replay === null || replayAlertId !== alert.id) {
        replay?.destroy();
        replay = map === null ? null : mountReplay(alert, map);
        replayAlertId = alert.id;
      }
      if (replay !== null) parts.push(replay.element);
    }

    // The receivers were off for part of this window: say so, and say it was not counted.
    const gap = overlappingGap(snapshot, alert, now);
    if (gap !== null) {
      parts.push(
        h("div", { class: "watch-gapnote" }, `Receivers offline ${gap.from}–${gap.to}Z — not counted`),
      );
    }

    let otherEl: HTMLElement | null = null;

    if (alert.kind === "sanctioned_stop") {
      // A stopped ship has no reach story to tell: show who is nearest to it.
      const near = nearbyShips(snapshot, alert, 15);
      parts.push(
        section(
          "Ships nearby",
          near.length,
          near.length === 0 ? h("div", { class: "watch-none" }, "No ships heard nearby") : contactTable(snapshot, near),
        ),
      );
    } else if (alert.kind === "spoof") {
      parts.push(spoofSection(snapshot, alert));
    } else if (alert.kind === "dark") {
      const sorted = sortedContacts(alert.contacts);
      const met = sorted.filter((contact) => contact.could_meet === true);
      const rest = sorted.filter((contact) => contact.could_meet !== true);
      parts.push(
        section(
          "Could have met",
          met.length,
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
      const alsoCount = rest.length + others.length;
      parts.push(
        h(
          "details",
          { class: "watch-also" },
          h("summary", null, `Also in reach: ${alsoCount}`),
          also.length === 0 ? h("div", { class: "watch-none" }, "None") : contactTable(snapshot, also),
        ),
      );
    } else {
      const contacts = sortedContacts(alert.contacts);
      otherEl = h(
        "div",
        { class: "watch-other" },
        `Other ships in reach: ${otherShipsInReach(snapshot, alert, atMs)}`,
      );
      parts.push(
        section(
          "Nearby vessels",
          contacts.length,
          contacts.length === 0 ? h("div", { class: "watch-none" }, "No ships in reach") : contactTable(snapshot, contacts),
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
      ...field("Ships heard", h("span", { class: "watch-mono" }, String(heard))),
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

  function loadRadar(): Promise<{ unseen: HormuzUnseenFile; history: HormuzHistoryFile }> {
    if (radarData === null) {
      radarData = Promise.all([fetchHormuzUnseen(abort.signal), fetchHormuzHistory(abort.signal)]).then(
        ([unseen, history]) => ({ unseen, history }),
      );
      radarData.catch(() => {
        radarData = null;
      });
    }
    return radarData;
  }

  function disposeRadar(): void {
    radarToken += 1;
    radarPanel?.destroy();
    radarPanel = null;
    radarSelectedEncounter = null;
  }

  function showRadar(alert: WatchAlert | null): void {
    disposeRadar();
    const token = radarToken;
    detailBody.replaceChildren(
      h(
        "div",
        { class: "watch-skel-panel", "aria-hidden": "true" },
        ...skeletonRows(6),
      ),
    );
    setAttrib(true);
    // Fly now; the density layer arrives with the data.
    map?.showRegion({
      bounds: [54.0, 22.0, 60.5, 27.5],
      areaId: "HORMUZ_STRAIT",
      alertId: alert?.id ?? null,
    });
    void loadRadar()
      .then(({ unseen, history }) => {
        if (destroyed || token !== radarToken || map === null) return;
        const evidenceDay = typeof alert?.evidence["day"] === "string" ? (alert.evidence["day"] as string) : null;
        map.showRegion({ bounds: radarBounds(unseen), areaId: "HORMUZ_STRAIT", alertId: alert?.id ?? null });
        map.setRadar(radarMapView(unseen, history, null));
        const handle = map;
        const panel = buildRadarPanel({
          unseen,
          history,
          restDay: evidenceDay ?? latestWellCoveredDay(unseen),
          autoplay: true,
          onIntensity: (k) => handle.setRadarIntensity(k),
          onEncounter: (i) => focusEncounter(history, i),
          onHotspot: (i) => {
            const spot = history.hotspots[i];
            if (spot === undefined) return;
            handle.focusContact({
              mmsi: 0,
              name: `Loitering hotspot · ${spot.n} events`,
              lat: spot.lat,
              lon: spot.lon,
              sanctioned: false,
            });
          },
        });
        radarPanel = panel;
        detailBody.replaceChildren(panel.element);

        // Map markers select rows too.
        radarMarkerHandler = (kind, index) => {
          if (kind === "encounter") {
            panel.markEncounter(index);
            focusEncounter(history, index);
          } else {
            const spot = history.hotspots[index];
            if (spot === undefined) return;
            handle.focusContact({
              mmsi: 0,
              name: `Loitering hotspot · ${spot.n} events`,
              lat: spot.lat,
              lon: spot.lon,
              sanctioned: false,
            });
          }
        };
      })
      .catch(() => {
        if (destroyed || token !== radarToken) return;
        detailBody.replaceChildren(h("div", { class: "watch-none watch-area-empty" }, "Radar picture unavailable"));
      });
  }

  let radarMarkerHandler: ((kind: "encounter" | "hotspot", index: number) => void) | null = null;

  function focusEncounter(history: HormuzHistoryFile, index: number): void {
    const enc = history.encounters[index];
    if (enc === undefined || map === null) return;
    radarSelectedEncounter = index;
    map.focusContact({
      mmsi: 0,
      name: `${partyLabel(enc.vessel.name, enc.vessel.mmsi)} × ${partyLabel(enc.other.name, enc.other.mmsi)}`,
      lat: enc.lat,
      lon: enc.lon,
      sanctioned: false,
    });
  }

  function partyLabel(name: string | null | undefined, mmsi: string | null | undefined): string {
    const trimmed = name?.trim();
    return trimmed ? trimmed : mmsi ? `MMSI ${mmsi}` : "Unnamed";
  }

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
      (alert === null && area !== null && area.id === "HORMUZ_STRAIT");
    const areaMode = alert === null && area !== null && !radarMode;
    const open = alert !== null || area !== null;
    if (alert === null || alert.kind !== "dark") dropReplay();

    main.classList.toggle("is-focus", open);
    details.hidden = !open;
    markRows();
    for (const row of areasList.querySelectorAll<HTMLButtonElement>(".watch-area-row")) {
      if (row.dataset.areaId === area?.id && alert === null) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }

    if (!open) {
      detailBody.replaceChildren();
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

    const initial = pending ?? { alertId: selectedId, areaId: selectedAreaId };
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
      stream?.close();
      stream = null;
      feed?.close();
      feed = null;
      abort.abort();
      dropReplay();
      searchFocusOff?.();
      searchFocusOff = null;
      setDossierTrackRenderer(null);
      closeDossier();
      map?.destroy();
      map = null;
    },
  };
}
