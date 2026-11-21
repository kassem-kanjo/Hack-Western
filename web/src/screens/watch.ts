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
import { fetchWatch, watchFixtureUrl, watchStream } from "../api";
import { h } from "../components/dom";
import { topBar } from "../components/TopBar";
import { fmtUtc } from "../format";
import { timeAgo } from "../model/liveFacts";
import {
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
}

export interface WatchScreen {
  destroy(): void;
  /** The router calls this when only the alert part of the hash changed. */
  select(alertId: string | null): void;
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
  const match = /^\/watch\/alert\/(.+)$/.exec(path);
  if (match === null) return null;
  try {
    return { alertId: decodeURIComponent(match[1] as string) };
  } catch {
    // A malformed escape is not an alert id; the world view is the fallback.
    return null;
  }
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
};

/** Toggle order: what Kassem asked for first, then the remaining contract kind. */
const KIND_ORDER: WatchAlertKind[] = ["dark", "sanctioned_stop", "spoof", "meeting", "jump"];

/** A vessel name, or the contract's honest placeholder. */
function alertName(alert: WatchAlert): string {
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
  const start = Date.parse(alert.t_start);
  if (!Number.isFinite(start)) return true;
  return start >= nowMs - hours * 3_600_000;
}

/** Area and window only: the base set the type counts are drawn from. */
function scopedAlerts(data: WatchResponse, state: FilterState): WatchAlert[] {
  const now = snapshotNow(data);
  return data.alerts.filter((alert) => {
    if (state.areaId !== null && alert.area_id !== state.areaId) return false;
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

/* ---- screen --------------------------------------------------------------- */

/**
 * Paint the watch screen into `app`. The shell (bar, map box, events rail)
 * exists before the data does, so the loading line never flashes an empty split.
 */
export function renderWatch(app: HTMLElement, route: WatchRoute): WatchScreen {
  const abort = new AbortController();
  /** `?fixture` opens the sample snapshot instead of the API. Nothing else does. */
  const fixtureMode = new URLSearchParams(location.search).has("fixture");
  let destroyed = false;

  const mapDiv = h("div", { class: "watch-map" });
  const liveChip = h("div", { class: "watch-live", role: "status" }, "Live");
  const attrib = watchAttribution();
  const stage = h("section", { class: "stage watch-stage" }, mapDiv, liveChip, attrib);

  const eventsCount = h("span", { class: "watch-count" }, "\u2014");
  const filtersEl = h("div", { class: "watch-filters" });
  const rowsEl = h("ol", { class: "watch-rows" });
  const hiddenRoutine = h("span", { class: "watch-foot-hidden" });
  const exportBtn = h("button", { class: "watch-export", type: "button" }, "Export CSV");
  const footEl = h(
    "div",
    { class: "watch-foot" },
    hiddenRoutine,
    h("span", { class: "watch-foot-sep", "aria-hidden": "true" }, "\u00b7"),
    exportBtn,
  );
  const events = h(
    "aside",
    { class: "watch-events" },
    h(
      "div",
      { class: "watch-events-head" },
      h("h2", null, "Events"),
      eventsCount,
    ),
    filtersEl,
    rowsEl,
    footEl,
  );

  const detailBody = h("div", { class: "watch-details-body" });
  const back = h(
    "button",
    { class: "watch-back", type: "button", "aria-label": "Back to events" },
    "\u2190 Events",
  );
  const details = h(
    "aside",
    { class: "watch-details", hidden: true },
    h(
      "div",
      { class: "watch-details-head" },
      back,
      h("span", { class: "watch-details-title" }, "Event details"),
    ),
    detailBody,
  );

  const main = h(
    "main",
    { class: "main watch-main" },
    h("div", { class: "empty" }, "Loading the watch snapshot..."),
  );

  app.replaceChildren(
    topBar({ active: "watch", mode: "LIVE", generatedAt: null, caseHref: null, demoHref: null }),
    main,
  );

  let data: WatchResponse | null = null;
  let selectedId: string | null = route.alertId;
  let appliedId: string | null = null;
  let map: WatchMapHandle | null = null;
  let stream: WatchStream | null = null;
  let panelParts: {
    alertId: string;
    duration: HTMLElement;
    radius: HTMLElement | null;
    other: HTMLElement;
  } | null = null;
  let panelTicker: number | null = null;
  let chipTicker: number | null = null;
  let refreshing = false;
  let pendingRefresh = false;
  /** The selection to apply once the first snapshot exists. */
  let pending: string | null = route.alertId;

  const state: FilterState = { kinds: new Set(KIND_ORDER), areaId: null, windowH: 12 };
  let kindButtons: { kind: WatchAlertKind; button: HTMLButtonElement; count: HTMLElement }[] = [];
  let areaSelect: HTMLSelectElement | null = null;
  let windowButtons: { hours: WindowHours; button: HTMLButtonElement }[] = [];
  /** What the export button writes: exactly the rows on screen. */
  let exportRows: WatchAlert[] = [];

  // ---- clock: the one age label and the live duration ----------------------

  function paintChip(nowMs: number): void {
    if (data === null) {
      liveChip.textContent = "Live";
      return;
    }
    liveChip.replaceChildren(
      h("span", { class: "watch-live-dot", "aria-hidden": "true" }),
      `Live \u00b7 updated ${timeAgo(data.generated_at, nowMs)}`,
    );
    liveChip.title = fixtureMode
      ? "Sample snapshot (opened with ?fixture)"
      : "Updated whenever the recorder writes a new snapshot";
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
      panelParts.other.textContent = `Other ships in reach: ${otherShipsInReach(data, current, reachTimeMs(current, now))}`;
      paintChip(now);
    }, 1000);
  }

  // ---- events rail ----------------------------------------------------------

  function markRows(): void {
    for (const row of rowsEl.querySelectorAll<HTMLButtonElement>(".watch-row")) {
      const id = row.dataset.alertId;
      if (id !== undefined && id === selectedId) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }
  }

  function setPressed(button: HTMLButtonElement, pressed: boolean): void {
    button.setAttribute("aria-pressed", pressed ? "true" : "false");
  }

  /** Counts and control states from the current area + window scope. */
  function syncFilters(snapshot: WatchResponse): void {
    const counts = kindCounts(scopedAlerts(snapshot, state));
    for (const entry of kindButtons) {
      entry.count.textContent = String(counts.get(entry.kind) ?? 0);
      setPressed(entry.button, state.kinds.has(entry.kind));
    }
    if (areaSelect !== null) areaSelect.value = state.areaId ?? "";
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
          ? h("span", {
              class: "watch-row-live",
              title: "Ongoing",
              "aria-label": "ongoing",
            })
          : null,
        h(
          "time",
          { class: "watch-row-time", datetime: alert.t_start },
          h("span", { class: "watch-row-utc" }, fmtUtcZ(alert.t_start)),
          h("span", { class: "watch-row-rel" }, timeAgo(alert.t_start, now)),
        ),
        h("span", { class: "watch-row-tag" }, KIND_NAME[alert.kind]),
        h("span", { class: "watch-row-area" }, areaName(snapshot, alert.area_id)),
      );
      row.addEventListener("click", () => {
        location.hash = watchHref(alert.id);
      });
      rowsEl.append(h("li", null, row));
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

    const select = h("select", { class: "watch-select", "aria-label": "Area" });
    select.append(h("option", { value: "" }, "All areas"));
    const watched = snapshot.areas.filter((area) => area.kind === "watched");
    const listening = snapshot.areas.filter((area) => area.kind === "listening");
    if (watched.length > 0) {
      const group = h("optgroup", { label: "Watched" });
      for (const area of watched) {
        group.append(h("option", { value: area.id }, area.name));
      }
      select.append(group);
    }
    if (listening.length > 0) {
      const group = h("optgroup", { label: "Listening" });
      for (const area of listening) {
        group.append(h("option", { value: area.id }, area.name));
      }
      select.append(group);
    }
    select.value = state.areaId ?? "";
    select.addEventListener("change", () => {
      state.areaId = select.value === "" ? null : select.value;
      if (data !== null) {
        syncFilters(data);
        renderRows(data);
      }
    });
    areaSelect = select;

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

    filtersEl.replaceChildren(
      kinds,
      h("div", { class: "watch-filter-row" }, select, windowGroup),
    );
  }

  function renderEvents(snapshot: WatchResponse): void {
    buildFilters(snapshot);
    syncFilters(snapshot);
    renderRows(snapshot);
  }

  exportBtn.addEventListener("click", () => {
    if (data !== null) downloadCsv(exportRows, data);
  });

  back.addEventListener("click", () => {
    location.hash = "#/watch";
  });

  // ---- detail panel ---------------------------------------------------------

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
        h(
          "span",
          { class: "watch-mono" },
          `${alert.last.lat.toFixed(4)}, ${alert.last.lon.toFixed(4)}`,
        ),
      ),
      ...(radiusLabel !== null ? field(radiusLabel, radiusEl as HTMLElement) : []),
    );

    const contacts = sortedContacts(alert.contacts);
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
          h("th", { scope: "col" }, "Dist (km)"),
          h("th", { scope: "col" }, "Status"),
          h("th", { scope: "col" }, "List"),
        ),
      ),
      body,
    );

    for (const contact of contacts) {
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
        for (const current of table.querySelectorAll<HTMLTableRowElement>("tbody tr")) {
          current.removeAttribute("aria-current");
        }
        row.setAttribute("aria-current", "true");
      };
      row.addEventListener("click", ring);
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        ring();
      });
      body.append(row);
    }

    const otherEl = h(
      "div",
      { class: "watch-other" },
      `Other ships in reach: ${otherShipsInReach(snapshot, alert, atMs)}`,
    );

    detailBody.replaceChildren(
      fields,
      h(
        "section",
        { class: "watch-nearby" },
        h("h3", null, "Nearby vessels"),
        contacts.length === 0 ? h("div", { class: "watch-none" }, "No ships in reach") : table,
      ),
      otherEl,
    );

    panelParts = {
      alertId: alert.id,
      duration,
      radius: radiusEl,
      other: otherEl,
    };
  }

  // ---- selection ------------------------------------------------------------

  function applySelection(options: { keepCamera: boolean }): void {
    if (data === null) return;
    const alert =
      selectedId === null ? null : (data.alerts.find((entry) => entry.id === selectedId) ?? null);

    main.classList.toggle("is-focus", alert !== null);
    details.hidden = alert === null;
    markRows();

    if (alert === null) {
      detailBody.replaceChildren();
      panelParts = null;
      stopPanelTicker();
      appliedId = null;
      map?.showWorld();
      return;
    }

    if (appliedId !== alert.id) details.scrollTop = 0;
    renderDetails(data, alert);
    map?.focusAlert(alert, {
      animate: !options.keepCamera,
      moveCamera: !options.keepCamera,
    });
    startPanelTicker(alert);
    appliedId = alert.id;
  }

  function select(alertId: string | null): void {
    if (destroyed) return;
    selectedId = alertId;
    if (data === null) {
      pending = alertId;
      return;
    }
    pending = null;
    applySelection({ keepCamera: alertId !== null && alertId === appliedId });
  }

  // ---- data -----------------------------------------------------------------

  function build(snapshot: WatchResponse): void {
    data = snapshot;

    const mapHost = h("div", { class: "watch-map" });
    mapDiv.replaceWith(mapHost);
    // The stage keeps a reference to the live node for later selects.
    map = mountWatchMap({
      container: mapHost,
      onSelectAlert: (id) => {
        location.hash = watchHref(id);
      },
    });

    main.replaceChildren(stage, events, details);
    map.setData(snapshot);
    renderEvents(snapshot);
    paintChip(Date.now());

    const initial = pending ?? selectedId;
    selectedId = initial;
    if (initial !== null) {
      const exists = snapshot.alerts.some((alert) => alert.id === initial);
      if (!exists) selectedId = null;
    }
    pending = null;
    appliedId = null;
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
        chipTicker = window.setInterval(() => paintChip(Date.now()), 20_000);
        if (!fixtureMode) {
          stream = watchStream(() => {
            void load();
          });
        }
      } else {
        data = response;
        map?.setData(response);
        renderEvents(response);
        paintChip(Date.now());
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

  // Escape always returns to the world view; the presentation overlay owns the
  // key while it is open.
  function onKeydown(event: KeyboardEvent): void {
    if (destroyed || event.key !== "Escape") return;
    if (document.body.classList.contains("is-presenting")) return;
    if (selectedId === null) return;
    event.preventDefault();
    location.hash = "#/watch";
  }
  document.addEventListener("keydown", onKeydown);

  void load();

  return {
    select,
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      document.removeEventListener("keydown", onKeydown);
      stopPanelTicker();
      if (chipTicker !== null) {
        window.clearInterval(chipTicker);
        chipTicker = null;
      }
      stream?.close();
      stream = null;
      abort.abort();
      map?.destroy();
      map = null;
    },
  };
}
