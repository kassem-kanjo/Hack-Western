/**
 * Alert replay (#185): the dark period, played back in ten seconds.
 *
 * One alert, one clock. The scrubber spans the trail's first fix to
 * `t_end` (or now, while the alert is ongoing), and the clock is the contract's
 * own: while the playhead is on the trail the line draws up to it; from the last
 * fix the reach grows as `speed_cap_kn * 1.852 * hours(t - last.t)`, narrowed by
 * `next` exactly as docs/watch-contract.md defines the lens. Contacts enter the
 * drawing the moment the reach first contains them, and a closed gap drops its
 * reappearance point in at `t_end`, joined to the last fix by the dashed gap
 * line. Reduced motion gets the finished picture, no moving ink.
 *
 * The component owns three GeoJSON sources (`replay-trail`, `replay-reach`,
 * `replay-contacts`) and every layer drawn from them, so the map module needs no
 * knowledge of the replay: its whole hook is `replayMap` on `WatchMapHandle`
 * (one interface line, one object-literal line). The screen's hook is the mount.
 *
 * Geometry is not re-derived here: `reachRing`, `reachRadiusKm` and
 * `insideReach` are the same functions the focus view draws with, so the replay
 * and the static focus can never disagree about what is reachable.
 */

import "./replay.css";

import type {
  CircleLayerSpecification,
  FillLayerSpecification,
  GeoJSONSource,
  LineLayerSpecification,
  Map as MapLibreMap,
} from "maplibre-gl";

import type { WatchAlert } from "../api";
import { fmtUtc } from "../format";
import { themeColor } from "../map/theme";
import {
  insideReach,
  reachRadiusKm,
  reachRing,
  type WatchMapHandle,
} from "../map/watchMap";
import { h } from "./dom";

/** How long one pass of the whole window takes, regardless of its span. */
export const REPLAY_PLAY_MS = 10_000;

/** The three sources this component owns. Nothing else writes to them. */
export const REPLAY_SOURCES = ["replay-trail", "replay-reach", "replay-contacts"] as const;

const TRAIL_SOURCE = REPLAY_SOURCES[0];
const REACH_SOURCE = REPLAY_SOURCES[1];
const CONTACT_SOURCE = REPLAY_SOURCES[2];

/** Layers per source. Trail carries two shapes: the track and the gap. */
const LAYERS = {
  reachFill: "replay-reach-fill",
  reachEdge: "replay-reach-edge",
  trail: "replay-trail-line",
  gap: "replay-gap-line",
  head: "replay-trail-head",
  next: "replay-next",
  contacts: "replay-contacts-dots",
} as const;

const ALL_LAYERS = [
  LAYERS.reachFill,
  LAYERS.reachEdge,
  LAYERS.trail,
  LAYERS.gap,
  LAYERS.head,
  LAYERS.next,
  LAYERS.contacts,
];

/**
 * A watch alert as the replay reads it. `trail` is in the contract (and the
 * snapshot) but not yet in the API type, so it is read as `unknown` here: an
 * older snapshot without trails simply has nothing to draw.
 */
export type ReplayAlert = WatchAlert & { readonly trail?: unknown };

/** One parsed trail point. `t` is milliseconds, the trail's `t` is ISO. */
interface TrailFix {
  lon: number;
  lat: number;
  t: number;
}

interface ReplayProps {
  role?: string;
  mmsi?: number;
  watch?: number;
  meet?: number;
}

type ReplayCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, ReplayProps>;

export interface ReplayView {
  /** The panel (header, transport, scrubber). Hidden when there is nothing to replay. */
  element: HTMLElement;
  /** Stop the clock and remove every layer and source this view added. Idempotent. */
  destroy(): void;
}

/* ---- reading (pure) ------------------------------------------------------- */

function parseMs(iso: string | null): number | null {
  if (iso === null) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * The contract's trail, tolerantly: `[[lon, lat, "t"], ...]` in time order. A
 * malformed point is dropped rather than shifting the whole path, and the list
 * is sorted because a replay that appears to run backwards is worse than no
 * replay at all.
 */
export function readTrail(alert: ReplayAlert): TrailFix[] {
  const raw = alert.trail;
  if (!Array.isArray(raw)) return [];
  const fixes: TrailFix[] = [];
  for (const entry of raw) {
    if (!Array.isArray(entry) || entry.length < 3) continue;
    const lon = entry[0];
    const lat = entry[1];
    const iso = entry[2];
    if (typeof lon !== "number" || typeof lat !== "number" || typeof iso !== "string") continue;
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) continue;
    fixes.push({ lon, lat, t });
  }
  fixes.sort((a, b) => a.t - b.t);
  return fixes;
}

/**
 * Where the drawn head is at `atMs`: the last fix, or a straight line between
 * the two fixes that bracket the playhead. The contract only promises decimated
 * points, so without this the line advances in visible steps.
 */
export function trailHead(fixes: TrailFix[], atMs: number): { lon: number; lat: number } | null {
  let index = -1;
  for (let i = 0; i < fixes.length; i += 1) {
    const fix = fixes[i] as TrailFix;
    if (fix.t <= atMs) index = i;
    else break;
  }
  if (index < 0) return null;
  const current = fixes[index] as TrailFix;
  const next = fixes[index + 1];
  if (next === undefined || atMs >= next.t) return { lon: current.lon, lat: current.lat };
  const fraction = (atMs - current.t) / Math.max(1, next.t - current.t);
  return {
    lon: current.lon + (next.lon - current.lon) * fraction,
    lat: current.lat + (next.lat - current.lat) * fraction,
  };
}

/**
 * Hours:minutes once the window is an hour or more, otherwise minutes:seconds.
 * The total carries the unit ("7:02 h"); the elapsed side is the same shape.
 */
export function fmtReplayClock(ms: number, long: boolean): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (long) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}:${String(minutes).padStart(2, "0")}`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function emptyCollection(): ReplayCollection {
  return { type: "FeatureCollection", features: [] };
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/* ---- icons ---------------------------------------------------------------- */

const SVG_NS = "http://www.w3.org/2000/svg";

/** Play triangle or pause bars: two states, no icon font, no motion of its own. */
function transportIcon(playing: boolean): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", "10");
  svg.setAttribute("height", "10");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "replay-icon");

  if (playing) {
    for (const x of [2.5, 7]) {
      const bar = document.createElementNS(SVG_NS, "rect");
      bar.setAttribute("x", String(x));
      bar.setAttribute("y", "1.5");
      bar.setAttribute("width", "2");
      bar.setAttribute("height", "9");
      bar.setAttribute("fill", "currentColor");
      svg.append(bar);
    }
  } else {
    const triangle = document.createElementNS(SVG_NS, "path");
    triangle.setAttribute("d", "M2.5 1.5 L10 6 L2.5 10.5 Z");
    triangle.setAttribute("fill", "currentColor");
    svg.append(triangle);
  }
  return svg;
}

/* ---- map adapter ---------------------------------------------------------- */

/**
 * The tiny adapter between this component and MapLibre: install the three
 * sources and their layers once, then push FeatureCollections at them. It never
 * touches a watch layer, so the focus view can keep drawing underneath.
 */
function mapAdapter(map: MapLibreMap) {
  let installed = false;

  const colors = {
    sea: themeColor("--c-bg", "#0A1017"),
    text1: themeColor("--c-text-1", "#EAF0F6"),
    text3: themeColor("--c-text-3", "#8A9BAD"),
    ais: themeColor("--c-ais", "#4FD1B0"),
    gap: themeColor("--c-gap", "#FFB020"),
    prism: themeColor("--c-prism-stroke", "#A78BFA"),
    risk: themeColor("--c-risk-high", "#FF6B5E"),
  };

  function install(): boolean {
    // Sources first, each one only if missing: a half install (an earlier frame
    // that failed mid-way) must not be mistaken for a finished one.
    if (map.getSource(TRAIL_SOURCE) === undefined) {
      map.addSource(TRAIL_SOURCE, { type: "geojson", data: emptyCollection() });
    }
    if (map.getSource(REACH_SOURCE) === undefined) {
      map.addSource(REACH_SOURCE, { type: "geojson", data: emptyCollection() });
    }
    if (map.getSource(CONTACT_SOURCE) === undefined) {
      map.addSource(CONTACT_SOURCE, { type: "geojson", data: emptyCollection() });
    }

    // Reach first: it is the field the other two are read against.
    const reachFill: FillLayerSpecification = {
      id: LAYERS.reachFill,
      type: "fill",
      source: REACH_SOURCE,
      paint: { "fill-color": colors.prism, "fill-opacity": 0.07 },
    };
    const reachEdge: LineLayerSpecification = {
      id: LAYERS.reachEdge,
      type: "line",
      source: REACH_SOURCE,
      paint: {
        "line-color": colors.prism,
        "line-opacity": 0.9,
        "line-width": 1,
      },
    };
    // The ship's own track: AIS colour, hairline, solid.
    const trailLine: LineLayerSpecification = {
      id: LAYERS.trail,
      type: "line",
      source: TRAIL_SOURCE,
      filter: ["==", ["get", "role"], "track"],
      layout: { "line-join": "round" },
      paint: {
        "line-color": colors.ais,
        "line-opacity": 0.85,
        "line-width": 1.25,
      },
    };
    // The gap: the contract's dashed last fix -> reappearance line.
    const gapLine: LineLayerSpecification = {
      id: LAYERS.gap,
      type: "line",
      source: TRAIL_SOURCE,
      filter: ["==", ["get", "role"], "gap"],
      paint: {
        "line-color": colors.gap,
        "line-opacity": 0.95,
        "line-width": 1.25,
        "line-dasharray": [2, 3],
      },
    };
    // The moving head of the trail, and the reappearance point.
    const headDot: CircleLayerSpecification = {
      id: LAYERS.head,
      type: "circle",
      source: TRAIL_SOURCE,
      filter: ["==", ["get", "role"], "head"],
      paint: {
        "circle-radius": 2.2,
        "circle-color": colors.ais,
        "circle-stroke-color": colors.sea,
        "circle-stroke-width": 1,
      },
    };
    const nextDot: CircleLayerSpecification = {
      id: LAYERS.next,
      type: "circle",
      source: TRAIL_SOURCE,
      filter: ["==", ["get", "role"], "next"],
      paint: {
        "circle-radius": 3.2,
        "circle-color": colors.sea,
        "circle-stroke-color": colors.text1,
        "circle-stroke-width": 1.2,
      },
    };
    // Contacts are rings around the hull's own dot: in reach, not a second dot.
    const contactDots: CircleLayerSpecification = {
      id: LAYERS.contacts,
      type: "circle",
      source: CONTACT_SOURCE,
      paint: {
        "circle-radius": ["case", ["==", ["get", "watch"], 1], 3.6, 3],
        "circle-color": colors.sea,
        "circle-opacity": 0,
        "circle-stroke-color": [
          "case",
          ["==", ["get", "watch"], 1],
          colors.risk,
          ["==", ["get", "meet"], 1],
          colors.gap,
          colors.text3,
        ],
        "circle-stroke-width": ["case", ["==", ["get", "watch"], 1], 1.6, 1.2],
      },
    };

    for (const layer of [reachFill, reachEdge, trailLine, gapLine, headDot, nextDot, contactDots]) {
      if (map.getLayer(layer.id) === undefined) map.addLayer(layer);
    }

    installed = true;
    return true;
  }

  function ensure(): boolean {
    try {
      if (installed && map.getSource(TRAIL_SOURCE) !== undefined) return true;
      installed = false;
      return install();
    } catch {
      // The style is being replaced or the map is closing; the next frame retries.
      installed = false;
      return false;
    }
  }

  function setData(id: string, data: ReplayCollection): void {
    const source = map.getSource(id) as GeoJSONSource | undefined;
    if (source === undefined) return;
    try {
      void source.setData(data);
    } catch {
      installed = false;
    }
  }

  function remove(): void {
    for (const id of [...ALL_LAYERS].reverse()) {
      try {
        if (map.getLayer(id) !== undefined) map.removeLayer(id);
      } catch {
        // The map is already gone; nothing left to take down.
      }
    }
    for (const id of [...REPLAY_SOURCES].reverse()) {
      try {
        if (map.getSource(id) !== undefined) map.removeSource(id);
      } catch {
        // Same.
      }
    }
    installed = false;
  }

  return { colors, ensure, setData, remove };
}

/* ---- feature builders (pure) ---------------------------------------------- */

/** The drawn track, its moving head, and (at `t_end`) the gap and reappearance point. */
function trailFeatures(alert: ReplayAlert, fixes: TrailFix[], atMs: number, endMs: number | null): ReplayCollection {
  const features: GeoJSON.Feature<GeoJSON.Geometry, ReplayProps>[] = [];
  const coords: [number, number][] = [];
  for (const fix of fixes) {
    if (fix.t <= atMs) coords.push([fix.lon, fix.lat]);
  }

  const head = trailHead(fixes, atMs);
  if (head !== null) {
    const tail = coords[coords.length - 1];
    if (tail === undefined || tail[0] !== head.lon || tail[1] !== head.lat) {
      coords.push([head.lon, head.lat]);
    }
  }

  if (coords.length >= 2) {
    features.push({
      type: "Feature",
      properties: { role: "track" },
      geometry: { type: "LineString", coordinates: coords },
    });
  }
  if (head !== null) {
    features.push({
      type: "Feature",
      properties: { role: "head" },
      geometry: { type: "Point", coordinates: [head.lon, head.lat] },
    });
  }

  // The reappearance is a fact only once the clock reaches the end of the gap.
  if (alert.next !== null && endMs !== null && atMs >= endMs) {
    features.push({
      type: "Feature",
      properties: { role: "gap" },
      geometry: {
        type: "LineString",
        coordinates: [
          [alert.last.lon, alert.last.lat],
          [alert.next.lon, alert.next.lat],
        ],
      },
    });
    features.push({
      type: "Feature",
      properties: { role: "next" },
      geometry: { type: "Point", coordinates: [alert.next.lon, alert.next.lat] },
    });
  }

  return { type: "FeatureCollection", features };
}

function reachFeatures(alert: ReplayAlert, atMs: number): ReplayCollection {
  const ring = reachRing(alert, atMs);
  if (ring === null || ring.length < 3) return emptyCollection();
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [ring] },
      },
    ],
  };
}

/** Every contact the reach contains at this instant: they enter when the ring reaches them. */
function contactFeatures(alert: ReplayAlert, atMs: number): ReplayCollection {
  const features: GeoJSON.Feature<GeoJSON.Geometry, ReplayProps>[] = [];
  for (const contact of alert.contacts) {
    if (!Number.isFinite(contact.lat) || !Number.isFinite(contact.lon)) continue;
    if (!insideReach(alert, contact.lat, contact.lon, atMs)) continue;
    features.push({
      type: "Feature",
      properties: {
        mmsi: contact.mmsi,
        watch: contact.watchlist === "sanctioned" ? 1 : 0,
        meet: contact.could_meet === true ? 1 : 0,
      },
      geometry: { type: "Point", coordinates: [contact.lon, contact.lat] },
    });
  }
  return { type: "FeatureCollection", features };
}

/* ---- mount ---------------------------------------------------------------- */

/**
 * Mount the replay for one alert against one map. The caller keeps the returned
 * view and calls `destroy()` when the selection changes or the screen closes;
 * calling this again for the same alert starts a fresh clock.
 */
export function mountReplay(alert: ReplayAlert, handle: WatchMapHandle): ReplayView {
  const fixes = readTrail(alert);
  const startMs = parseMs(alert.t_start);
  const lastMs = parseMs(alert.last.t);
  const endMs = alert.t_end === null ? null : parseMs(alert.t_end);
  const capKn = Number.isFinite(alert.speed_cap_kn) ? Math.max(0, alert.speed_cap_kn) : 0;

  const nowMs = Date.now();
  const clockEnd = endMs ?? nowMs;
  const t1 = Math.max(clockEnd, lastMs ?? clockEnd);
  const firstFix = fixes[0] as TrailFix | undefined;
  const t0 = Math.min(firstFix?.t ?? startMs ?? lastMs ?? t1 - 1, t1);
  const spanMs = Math.max(1, t1 - t0);

  // A radar alert has no track, no reach and no named contacts: there is
  // nothing to play. A short gap still gets a scrubber.
  const playable =
    spanMs >= 1000 && (fixes.length >= 1 || (lastMs !== null && capKn > 0));

  const reduced = prefersReducedMotion();
  const long = spanMs >= 3_600_000;

  let destroyed = false;
  let playing = false;
  let progress = reduced ? 1 : 0;
  let rafId: number | null = null;
  let lastFrame = 0;

  const count =
    fixes.length > 0
      ? `${fixes.length} ${fixes.length === 1 ? "fix" : "fixes"}`
      : `${alert.contacts.length} ${alert.contacts.length === 1 ? "contact" : "contacts"}`;
  const countEl = h("span", { class: "replay-count" }, count);
  const playBtn = h("button", {
    class: "replay-play",
    type: "button",
    "aria-label": "Play replay",
    title: "Play / pause the dark period",
  });
  const elapsedEl = h("span", { class: "replay-elapsed" }, "0:00");
  const totalEl = h("span", { class: "replay-total" }, `${fmtReplayClock(spanMs, long)}${long ? " h" : ""}`);
  const radiusEl = h(
    "span",
    { class: "replay-radius", title: "Reach radius at this moment" },
    "0 km",
  );
  const slider = h("input", {
    class: "replay-range",
    type: "range",
    min: 0,
    max: 1000,
    step: 1,
    value: 0,
    "aria-label": "Replay position",
    "aria-valuetext": "start",
  });
  const ticksEl = h("div", { class: "replay-ticks", "aria-hidden": "true" });

  /** A tick at a moment the playback passes; 1 px, titled, never a label. */
  function addTick(ms: number, cls: string, title: string): void {
    if (!Number.isFinite(ms)) return;
    const fraction = (ms - t0) / spanMs;
    if (!(fraction > 0.015 && fraction < 0.985)) return;
    const tick = h("i", { class: `replay-tick ${cls}`, title });
    tick.style.left = `${(fraction * 100).toFixed(2)}%`;
    ticksEl.append(tick);
  }
  if (lastMs !== null) addTick(lastMs, "is-last", "Last fix");
  if (endMs !== null && alert.next !== null) addTick(endMs, "is-next", "Reappears");

  const section = h(
    "section",
    { class: "replay", "aria-label": "Alert replay" },
    h("div", { class: "replay-head" }, h("h3", null, "Replay"), countEl),
    h(
      "div",
      { class: "replay-body" },
      h(
        "div",
        { class: "replay-bar" },
        playBtn,
        h(
          "span",
          { class: "replay-clock" },
          elapsedEl,
          h("span", { class: "replay-sep", "aria-hidden": "true" }, " / "),
          totalEl,
        ),
        radiusEl,
      ),
      h("div", { class: "replay-track" }, slider, ticksEl),
    ),
  );

  // Widened on purpose: a tree where the map hook was not carried would leave
  // this undefined, and a replay without a map is better than a crash.
  const map = handle.replayMap as MapLibreMap | undefined;
  const adapter = playable && map !== undefined ? mapAdapter(map) : null;

  function paintTransport(): void {
    playBtn.replaceChildren(transportIcon(playing));
    playBtn.setAttribute("aria-label", playing ? "Pause replay" : "Play replay");
  }

  function paintMap(atMs: number): void {
    if (adapter === null || destroyed) return;
    if (!adapter.ensure()) return;
    adapter.setData(TRAIL_SOURCE, trailFeatures(alert, fixes, atMs, endMs));
    adapter.setData(REACH_SOURCE, reachFeatures(alert, atMs));
    adapter.setData(CONTACT_SOURCE, contactFeatures(alert, atMs));
  }

  /** Push one position through the labels, the thumb and the map. */
  function setProgress(next: number): void {
    progress = Math.min(1, Math.max(0, Number.isFinite(next) ? next : 0));
    const atMs = t0 + spanMs * progress;
    const radiusKm = reachRadiusKm(alert, atMs);

    slider.value = String(Math.round(progress * 1000));
    slider.style.setProperty("--at", `${(progress * 100).toFixed(2)}%`);
    elapsedEl.textContent = fmtReplayClock(atMs - t0, long);
    radiusEl.textContent = `${Math.round(radiusKm)} km`;
    slider.setAttribute(
      "aria-valuetext",
      `${fmtUtc(new Date(atMs).toISOString())}Z, ${Math.round(radiusKm)} km reach`,
    );
    paintMap(atMs);
  }

  function stopClock(): void {
    playing = false;
    if (rafId !== null) {
      window.cancelAnimationFrame(rafId);
      rafId = null;
    }
    paintTransport();
  }

  function tick(now: number): void {
    rafId = null;
    if (!playing || destroyed) return;
    // A hidden tab pauses rAF; do not let the first frame after it skip the pass.
    const delta = Math.min(250, Math.max(0, now - lastFrame));
    lastFrame = now;
    const next = Math.min(1, progress + delta / REPLAY_PLAY_MS);
    setProgress(next);
    if (next >= 1) {
      playing = false;
      paintTransport();
      return;
    }
    rafId = window.requestAnimationFrame(tick);
  }

  playBtn.addEventListener("click", () => {
    if (destroyed) return;
    if (playing) {
      stopClock();
      return;
    }
    if (reduced) {
      setProgress(1);
      return;
    }
    if (progress >= 1) setProgress(0);
    playing = true;
    lastFrame = performance.now();
    paintTransport();
    rafId = window.requestAnimationFrame(tick);
  });

  // A drag seeks and holds: the clock never fights the thumb.
  slider.addEventListener("input", () => {
    if (destroyed) return;
    stopClock();
    const value = Number(slider.value);
    setProgress(Number.isFinite(value) ? value / 1000 : 0);
  });

  if (!playable) {
    section.hidden = true;
    playBtn.disabled = true;
  } else if (reduced) {
    playBtn.disabled = true;
    playBtn.title = "Reduced motion: showing the final state";
  }

  paintTransport();
  if (playable) setProgress(progress);

  return {
    element: section,
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      stopClock();
      adapter?.remove();
      section.remove();
    },
  };
}
