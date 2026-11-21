/**
 * Watch screen: the whole world, every ship we heard, and the few things worth
 * stopping for.
 *
 * World view: a dark map with every hull as a dim dot, the three listening
 * areas dashed, the four watched areas washed in, and a 320 px rail of alerts,
 * most severe first. One click (or one URL) opens focus: the map flies to the
 * last fix, the reach area grows, and a 300 px panel answers three questions in
 * that order - how long, how far, and who it could have met.
 *
 * No paragraphs anywhere: every line is a name, a time, a distance or a label
 * of at most six words. The URL is the selection (`#/watch/alert/<id>`), so a
 * reload reproduces the same view and the rail and the map cannot disagree.
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
 * The panel's one duration line. Hours and minutes, never seconds: a demo reads
 * a duration at 1 m, and "2 h 10 min" is the whole sentence.
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

/** The noun in front of the duration, per kind: "Dark 2 h 10 min". */
const DURATION_NOUN: Record<WatchAlertKind, string> = {
  dark: "Dark",
  spoof: "Spoofed",
  jump: "Jumped",
  meeting: "Alongside",
  sanctioned_stop: "Stopped",
};

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

/** Ongoing first, then severity, then the newest start. */
function sortedAlerts(alerts: WatchAlert[]): WatchAlert[] {
  return [...alerts].sort((a, b) => {
    const aOngoing = a.status === "ongoing" ? 1 : 0;
    const bOngoing = b.status === "ongoing" ? 1 : 0;
    if (aOngoing !== bOngoing) return bOngoing - aOngoing;
    if (b.severity !== a.severity) return b.severity - a.severity;
    const aStart = Date.parse(a.t_start);
    const bStart = Date.parse(b.t_start);
    if (Number.isFinite(aStart) && Number.isFinite(bStart) && aStart !== bStart) {
      return bStart - aStart;
    }
    return 0;
  });
}

/** How long an alert has been (or was) live, for the panel's first line. */
function durationMs(alert: WatchAlert, nowMs: number): number {
  const start = Date.parse(alert.t_start);
  if (!Number.isFinite(start)) return 0;
  const end = alert.t_end === null ? nowMs : Date.parse(alert.t_end);
  return Math.max(0, (Number.isFinite(end) ? end : nowMs) - start);
}

/** Sanctioned first, then the ones that could meet, then the closest. */
function sortedContacts(contacts: WatchContact[]): WatchContact[] {
  return [...contacts].sort((a, b) => {
    const aS = a.watchlist === "sanctioned" ? 1 : 0;
    const bS = b.watchlist === "sanctioned" ? 1 : 0;
    if (aS !== bS) return bS - aS;
    const aM = a.could_meet === true ? 1 : 0;
    const bM = b.could_meet === true ? 1 : 0;
    if (aM !== bM) return bM - aM;
    return a.min_dist_km - b.min_dist_km;
  });
}

/**
 * Ships in the reach area that are neither the alert's own hull nor a listed
 * contact: the honest remainder behind "Other ships in reach".
 */
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

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  return el;
}

/**
 * One kind glyph as a 16 px inline SVG, the same family language as the Live
 * screen: ring with a dashed tail (dark), crosshair (spoof), triangle (jump),
 * double ring (meeting), ring with a square (sanctioned stop).
 */
function kindGlyph(kind: WatchAlertKind): SVGSVGElement {
  const glyph = svgEl("svg", {
    class: `watch-glyph is-${kind}`,
    viewBox: "0 0 20 20",
    width: 16,
    height: 16,
    "aria-hidden": "true",
    focusable: "false",
  });
  const stroke = {
    fill: "none",
    stroke: "currentColor",
    "stroke-width": 2,
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
  } as const;

  if (kind === "dark") {
    glyph.append(
      svgEl("circle", { cx: 7, cy: 10, r: 5, ...stroke }),
      svgEl("path", { d: "M13 10h6", ...stroke, "stroke-dasharray": "2 2" }),
    );
  } else if (kind === "spoof") {
    glyph.append(
      svgEl("circle", { cx: 10, cy: 10, r: 4, ...stroke }),
      svgEl("path", { d: "M10 2.5v3M10 14.5v3M2.5 10h3M14.5 10h3", ...stroke }),
    );
  } else if (kind === "jump") {
    glyph.append(svgEl("path", { d: "M10 3 L17.5 16.5 H2.5 Z", ...stroke }));
  } else if (kind === "meeting") {
    glyph.append(
      svgEl("circle", { cx: 10, cy: 10, r: 4, ...stroke }),
      svgEl("circle", { cx: 10, cy: 10, r: 8, ...stroke }),
    );
  } else {
    glyph.append(
      svgEl("circle", { cx: 10, cy: 10, r: 7.5, ...stroke }),
      svgEl("rect", { x: 7.5, y: 7.5, width: 5, height: 5, fill: "currentColor", stroke: "none" }),
    );
  }
  return glyph;
}

/* ---- screen --------------------------------------------------------------- */

/**
 * Paint the watch screen into `app`. The shell (bar, map box, rail) exists
 * before the data does, so the loading line never flashes an empty layout.
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

  const rowsEl = h("ol", { class: "watch-rows" });
  const footEl = h("div", { class: "watch-foot" });
  const worldRail = h(
    "aside",
    { class: "rail watch-rail" },
    h(
      "div",
      { class: "watch-list-head" },
      h("h2", null, "Alerts"),
      h("span", { class: "meta" }, "most severe first"),
    ),
    rowsEl,
    footEl,
  );

  const panel = h("aside", { class: "rail watch-panel", hidden: true });
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
    radius: HTMLElement;
    other: HTMLElement;
  } | null = null;
  let panelTicker: number | null = null;
  let chipTicker: number | null = null;
  let refreshing = false;
  let pendingRefresh = false;
  /** The selection to apply once the first snapshot exists. */
  let pending: string | null = route.alertId;

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
      const atMs = reachTimeMs(current, now);
      panelParts.radius.textContent = `${Math.round(reachRadiusKm(current, atMs))} km`;
      panelParts.other.textContent = `Other ships in reach: ${otherShipsInReach(data, current, atMs)}`;
      paintChip(now);
    }, 1000);
  }

  // ---- rail -----------------------------------------------------------------

  function markRows(): void {
    for (const row of rowsEl.querySelectorAll<HTMLButtonElement>(".watch-row")) {
      const id = row.dataset.alertId;
      if (id !== undefined && id === selectedId) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }
  }

  function renderRows(snapshot: WatchResponse): void {
    const now = Date.now();
    rowsEl.replaceChildren();
    for (const alert of sortedAlerts(snapshot.alerts)) {
      const row = h(
        "button",
        {
          class: `watch-row is-${alert.kind}`,
          type: "button",
          "data-alert-id": alert.id,
          "aria-current": alert.id === selectedId ? "true" : null,
        },
        h("span", { class: "watch-row-icon", "aria-hidden": "true" }, kindGlyph(alert.kind)),
        h(
          "span",
          { class: "watch-row-main" },
          h(
            "span",
            { class: "watch-row-head" },
            h("span", { class: "watch-row-name" }, alertName(alert)),
            h("span", { class: "watch-row-label" }, shortLabel(alert.label)),
            alert.status === "ongoing" ? h("span", { class: "watch-pill" }, "ongoing") : null,
          ),
          h(
            "span",
            { class: "watch-row-meta" },
            `${areaName(snapshot, alert.area_id)} \u00b7 ${timeAgo(alert.last.t, now)}`,
          ),
        ),
      );
      row.addEventListener("click", () => {
        location.hash = watchHref(alert.id);
      });
      rowsEl.append(h("li", null, row));
    }
    footEl.textContent = `${snapshot.counts.hidden_routine} routine events hidden`;
  }

  // ---- panel ----------------------------------------------------------------

  function renderPanel(snapshot: WatchResponse, alert: WatchAlert): void {
    const now = Date.now();
    const atMs = reachTimeMs(alert, now);
    const radius = `${Math.round(reachRadiusKm(alert, atMs))} km`;
    const duration = h("span", { class: "watch-fact-v" }, fmtDuration(durationMs(alert, now)));
    const radiusEl = h("span", { class: "watch-fact-v" }, radius);
    const otherEl = h(
      "div",
      { class: "watch-other" },
      `Other ships in reach: ${otherShipsInReach(snapshot, alert, atMs)}`,
    );

    const met = sortedContacts(alert.contacts).filter((contact) => contact.could_meet === true);
    const metList = h("ul", { class: "watch-met-list" });
    for (const contact of met) {
      const sanctioned = contact.watchlist === "sanctioned";
      const button = h(
        "button",
        {
          class: `watch-met-row${sanctioned ? " is-sanctioned" : ""}`,
          type: "button",
        },
        h("span", { class: "watch-met-name" }, contactName(contact)),
        sanctioned ? h("span", { class: "watch-met-tag" }, "Sanctioned") : null,
        h("span", { class: "watch-met-dist" }, `${contact.min_dist_km.toFixed(1)} km`),
      );
      button.addEventListener("click", () => {
        if (map === null) return;
        const ship = contactShip(snapshot, contact);
        map.focusContact({
          mmsi: contact.mmsi,
          name: contact.name,
          lat: ship?.lat ?? contact.lat,
          lon: ship?.lon ?? contact.lon,
          sanctioned,
        });
        for (const row of metList.querySelectorAll<HTMLButtonElement>(".watch-met-row")) {
          row.removeAttribute("aria-current");
        }
        button.setAttribute("aria-current", "true");
      });
      metList.append(h("li", null, button));
    }

    const back = h(
      "button",
      { class: "watch-back", type: "button", "aria-label": "Back to all alerts" },
      "\u2190",
    );
    back.addEventListener("click", () => {
      location.hash = "#/watch";
    });

    panel.replaceChildren(
      h(
        "div",
        { class: "watch-panel-head" },
        h(
          "div",
          { class: "watch-panel-bar" },
          back,
          h("span", { class: `watch-panel-kind is-${alert.kind}` }, kindGlyph(alert.kind)),
          h("span", { class: "watch-panel-label" }, shortLabel(alert.label)),
          alert.status === "ongoing" ? h("span", { class: "watch-pill" }, "ongoing") : null,
        ),
        h("h2", { class: "watch-panel-name" }, alertName(alert)),
        h(
          "div",
          { class: "watch-panel-meta" },
          `${areaName(snapshot, alert.area_id)} \u00b7 MMSI ${alert.mmsi}`,
        ),
      ),
      h(
        "div",
        { class: "watch-facts" },
        h(
          "div",
          { class: "watch-fact" },
          h("span", { class: "watch-fact-k" }, DURATION_NOUN[alert.kind]),
          duration,
        ),
        h(
          "div",
          { class: "watch-fact" },
          h("span", { class: "watch-fact-k" }, "Could be within"),
          radiusEl,
        ),
      ),
      h(
        "section",
        { class: "watch-met" },
        h("h3", null, "Could have met"),
        met.length === 0 ? h("div", { class: "watch-none" }, "No ships in reach") : metList,
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
    worldRail.hidden = alert !== null;
    panel.hidden = alert === null;
    markRows();

    if (alert === null) {
      panel.replaceChildren();
      panelParts = null;
      stopPanelTicker();
      appliedId = null;
      map?.showWorld();
      return;
    }

    renderPanel(data, alert);
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

    main.replaceChildren(stage, worldRail, panel);
    map.setData(snapshot);
    renderRows(snapshot);
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
        renderRows(response);
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
