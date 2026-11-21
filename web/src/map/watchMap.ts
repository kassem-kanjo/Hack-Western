/**
 * Watch map: the whole world, every ship we heard, and the few things worth
 * looking at.
 *
 * Three densities, in the order the eye should read them:
 *  - every ship is a 1 px-radius dot at low opacity; watchlisted hulls are a
 *    little brighter and bigger (still a field, not a finding);
 *  - listening areas are dashed outlines with a name, watched areas a faint
 *    fill with a name;
 *  - alert ships are larger dots in their kind's colour (dark red, spoof
 *    violet, meeting and sanctioned stop amber) and are the only clickable
 *    things on the map.
 *
 * In focus one alert owns the stage: the reach area grows from the last fix to
 * `min(now, t_end)` (a disk, or the lens through the next fix), the last fix
 * carries its heading tick, the next fix is hollow, and the contacts that were
 * in reach are drawn under their own names when they are sanctioned.
 *
 * The offline style has no glyph server, so no layer uses text-field: every
 * word on the map is an HTML label positioned with map.project(). Shapes that
 * must stay a constant size on screen (alert dots, contacts, the fixes) are SVG
 * in the same overlay, for the same reason. The module knows nothing about
 * lists, panels or the router.
 */

import * as maplibregl from "maplibre-gl";

import type { WatchAlert, WatchArea, WatchContact, WatchResponse, WatchShip } from "../api";
import { offlineStyle } from "../basemap";
import { h } from "../components/dom";
import { rgba, themeColor } from "./theme";

type LngLat = [number, number];
type Bounds = [[number, number], [number, number]];

/** The whole world, with a little room for the far north and south. */
const WORLD_BOUNDS: Bounds = [
  [-170, -58],
  [178, 74],
];

/** Focus camera: close enough that a 60 km reach area is a shape, not a speck. */
const FOCUS_ZOOM = 8;
const FOCUS_MS = 1200;

/** The reach animation: 1 s, from the last fix to the display time. */
const REACH_MS = 1000;

/** Ongoing alerts re-grow every second, as the contract asks. */
const LIVE_STEP_MS = 1000;

/** Draw the last pixel of a lens: without it the reach vanishes at the next fix. */
const LENS_MIN_KM = 2;

/** 1 knot for 1 hour, in km. The contract's reach formula. */
export const KM_PER_KN_HOUR = 1.852;

/** Metres per degree of latitude (spherical), used for the local lens plane. */
const KM_PER_DEG_LAT = 110.574;
const KM_PER_DEG_LON = 111.32;

const SVG_NS = "http://www.w3.org/2000/svg";

/** Alerts are the only map things with a colour of their own. */
const ALERT_TOKEN: Record<WatchAlert["kind"], string> = {
  dark: "--c-risk-high",
  spoof: "--c-prism-stroke",
  jump: "--c-risk-high",
  meeting: "--c-gap",
  sanctioned_stop: "--c-gap",
};

/** Fallback when an alert kind is not one this build knows. */
const ALERT_FALLBACK = "#FF6B5E";

export interface FocusShip {
  mmsi: number;
  name: string | null;
  lat: number;
  lon: number;
  /** Sanctions hit: the ring and the name take the one warning colour. */
  sanctioned: boolean;
}

export interface WatchMapOptions {
  container: HTMLElement;
  /** An alert dot was clicked; the screen decides what that means for the URL. */
  onSelectAlert: (id: string) => void;
}

export interface WatchMapHandle {
  /** Replace the whole snapshot. The camera and the current focus are kept. */
  setData(data: WatchResponse): void;
  /**
   * Open one alert: fly to its last fix, draw the reach area, the fixes and the
   * contacts. `animate` replays the growth; `moveCamera` flies to the fix.
   */
  focusAlert(alert: WatchAlert, opts?: { animate?: boolean; moveCamera?: boolean }): void;
  /** Ring one hull (a contact row) without leaving the alert. null clears it. */
  focusContact(ship: FocusShip | null): void;
  /** Back to the world: clear every focus drawing and restore the camera. */
  showWorld(): void;
  resize(): void;
  destroy(): void;
}

/* ---- reach maths (pure; the screen reads the same numbers) ---------------- */

function parseMs(iso: string | null): number | null {
  if (iso === null) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * The instant the focus view describes.
 *
 * Ongoing: now, so the reach keeps growing. Ended without a next fix: the end
 * of the alert. Ended with a next fix: halfway through the gap, when the lens
 * is widest. The contract's formula evaluated exactly at the closing instant
 * collapses the lens onto the next fix (the ship is known to be there), which
 * would leave nothing on screen to describe "where it could have gone"; the
 * widest cross-section is the honest picture of the dark interval, and it is
 * still computed with the contract's formula and speed cap.
 */
export function reachTimeMs(alert: WatchAlert, nowMs: number): number {
  const lastT = parseMs(alert.last.t);
  const endRaw = alert.t_end === null ? nowMs : parseMs(alert.t_end);
  const capped = endRaw === null ? nowMs : Math.min(nowMs, endRaw);
  if (lastT === null) return capped;
  const base = Math.max(lastT, capped);

  if (alert.next === null) return base;
  const nextT = parseMs(alert.next.t);
  if (nextT === null) return base;

  const mid = lastT + (nextT - lastT) / 2;
  return Math.min(base, Math.max(lastT, mid));
}

/** Distance from the last fix that the speed cap allows by `atMs`. */
export function reachRadiusKm(alert: WatchAlert, atMs: number): number {
  const lastT = parseMs(alert.last.t);
  if (lastT === null || !Number.isFinite(alert.speed_cap_kn)) return 0;
  const hours = Math.max(0, atMs - lastT) / 3_600_000;
  return Math.max(0, alert.speed_cap_kn) * KM_PER_KN_HOUR * hours;
}

/** Distance from the next fix that the speed cap allows by `atMs`. */
export function nextRadiusKm(alert: WatchAlert, atMs: number): number {
  if (alert.next === null) return 0;
  const nextT = parseMs(alert.next.t);
  if (nextT === null || !Number.isFinite(alert.speed_cap_kn)) return 0;
  const hours = Math.max(0, nextT - atMs) / 3_600_000;
  return Math.max(0, alert.speed_cap_kn) * KM_PER_KN_HOUR * hours;
}

/** Great-circle km. Small enough inputs here that the sphere is plenty. */
function distanceKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const toRad = Math.PI / 180;
  const dLat = (bLat - aLat) * toRad;
  const dLon = (bLon - aLon) * toRad;
  const lat1 = aLat * toRad;
  const lat2 = bLat * toRad;
  const s =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Is this fix inside the reach at `atMs` (last disk, narrowed by the next one)? */
export function insideReach(
  alert: WatchAlert,
  lat: number,
  lon: number,
  atMs: number,
): boolean {
  const dLast = distanceKm(alert.last.lat, alert.last.lon, lat, lon);
  if (dLast > reachRadiusKm(alert, atMs) + 0.05) return false;
  if (alert.next === null) return true;
  const dNext = distanceKm(alert.next.lat, alert.next.lon, lat, lon);
  return dNext <= nextRadiusKm(alert, atMs) + 0.05;
}

/**
 * Unwrap a longitude difference around `originLon` so a lens that straddles the
 * antimeridian stays one continuous shape instead of jumping the long way.
 */
function lonOffset(lon: number, originLon: number): number {
  let delta = lon - originLon;
  while (delta > 180) delta -= 360;
  while (delta <= -180) delta += 360;
  return delta;
}

/** A closed ring of `n` points from a centre and radius, in local km. */
function circleRing(rKm: number, n = 96): [number, number][] {
  const ring: [number, number][] = [];
  for (let i = 0; i < n; i += 1) {
    const angle = (i / n) * Math.PI * 2;
    ring.push([Math.cos(angle) * rKm, Math.sin(angle) * rKm]);
  }
  ring.push(ring[0] as [number, number]);
  return ring;
}

/**
 * The reach polygon for one alert at one instant, in [lon, lat] pairs, or null
 * when there is nothing to draw (no time, no cap). A disk when there is no next
 * fix; the lens between the last fix and the next one when there is.
 */
export function reachRing(alert: WatchAlert, atMs: number): LngLat[] | null {
  const r1 = reachRadiusKm(alert, atMs);
  if (!(r1 > 0.02)) return null;

  const lat0 = alert.last.lat;
  const lon0 = alert.last.lon;
  const cos = Math.max(0.05, Math.cos((lat0 * Math.PI) / 180));
  const kx = KM_PER_DEG_LON * cos;
  const ky = KM_PER_DEG_LAT;
  const fromLocal = (x: number, y: number): LngLat => [lon0 + x / kx, lat0 + y / ky];

  const next = alert.next;
  if (next === null) {
    return circleRing(r1).map(([x, y]) => fromLocal(x, y));
  }

  const r2 = Math.max(LENS_MIN_KM, nextRadiusKm(alert, atMs));
  const dx = lonOffset(next.lon, lon0) * kx;
  const dy = (next.lat - lat0) * ky;
  const d = Math.hypot(dx, dy);

  // No overlap: the ship could not have made the next fix. Nothing to draw.
  if (d >= r1 + r2) return null;

  // One disk sits inside the other: the smaller one is the whole reach.
  if (d <= Math.abs(r1 - r2)) {
    if (r1 <= r2) return circleRing(r1).map(([x, y]) => fromLocal(x, y));
    return circleRing(r2).map(([x, y]) => fromLocal(dx + x, dy + y));
  }

  const a = (r1 * r1 - r2 * r2 + d * d) / (2 * d);
  const h2 = r1 * r1 - a * a;
  if (!(h2 > 0)) return null;

  const theta = Math.atan2(dy, dx);
  const alpha = Math.acos(Math.min(1, Math.max(-1, a / r1)));
  const beta = Math.acos(Math.min(1, Math.max(-1, (d - a) / r2)));
  const ring: LngLat[] = [];
  const steps = 44;

  // The arc of the last disk that the next fix can still be reached from ...
  for (let i = 0; i <= steps; i += 1) {
    const phi = theta - alpha + (2 * alpha * i) / steps;
    ring.push(fromLocal(Math.cos(phi) * r1, Math.sin(phi) * r1));
  }
  // ... then the arc of the next disk that the last fix could have come from.
  for (let i = 0; i <= steps; i += 1) {
    const phi = theta + Math.PI - beta + (2 * beta * i) / steps;
    ring.push(fromLocal(dx + Math.cos(phi) * r2, dy + Math.sin(phi) * r2));
  }

  return ring;
}

/* ---- SVG helpers ---------------------------------------------------------- */

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  return el;
}

/** A tooltip inside an SVG group; the text is set as textContent, never markup. */
function svgTitle(text: string): SVGTitleElement {
  const title = svgEl("title", {});
  title.textContent = text;
  return title;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** A ship name, with the contract's fallback when the broadcast had none. */
function displayName(name: string | null, mmsi: number): string {
  const trimmed = name?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : `Unnamed \u00b7 MMSI ${mmsi}`;
}

/* ---- mount ---------------------------------------------------------------- */

interface ShipProps {
  mmsi: number;
  watch: number;
}

interface AreaProps {
  kind: string;
}

function areaFeatures(areas: WatchArea[]): GeoJSON.FeatureCollection<GeoJSON.Polygon, AreaProps> {
  const features: GeoJSON.Feature<GeoJSON.Polygon, AreaProps>[] = [];
  for (const area of areas) {
    const [w, s, e, n] = area.bbox;
    features.push({
      type: "Feature",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [w, s],
            [e, s],
            [e, n],
            [w, n],
            [w, s],
          ],
        ],
      },
      properties: { kind: area.kind },
    });
  }
  return { type: "FeatureCollection", features };
}

function shipFeatures(ships: WatchShip[]): GeoJSON.FeatureCollection<GeoJSON.Point, ShipProps> {
  const features: GeoJSON.Feature<GeoJSON.Point, ShipProps>[] = [];
  for (const ship of ships) {
    if (!Number.isFinite(ship.lat) || !Number.isFinite(ship.lon)) continue;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [ship.lon, ship.lat] },
      properties: {
        mmsi: ship.mmsi,
        watch:
          ship.watchlist === "sanctioned" || ship.watchlist === "shadow_fleet" ? 1 : 0,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

/** One alert dot plus the group that carries it. */
interface AlertDot {
  alert: WatchAlert;
  group: SVGGElement;
}

/** A contact dot in focus, kept with its record so labels can be rebuilt. */
interface ContactDot {
  contact: WatchContact;
  circle: SVGCircleElement;
  label: HTMLElement | null;
}

export function mountWatchMap(opts: WatchMapOptions): WatchMapHandle {
  const colors = {
    text1: themeColor("--c-text-1", "#EAF0F6"),
    text2: themeColor("--c-text-2", "#B2BFCD"),
    text3: themeColor("--c-text-3", "#8A9BAD"),
    line: themeColor("--c-line", "#233245"),
    lineStrong: themeColor("--c-line-strong", "#34485F"),
    panel: themeColor("--c-panel", "#101923"),
    sea: themeColor("--c-bg", "#0A1017"),
    alert: themeColor("--c-risk-high", "#FF6B5E"),
    gap: themeColor("--c-gap", "#FFB020"),
    prism: themeColor("--c-prism-stroke", "#A78BFA"),
  };

  const alertColor = (kind: WatchAlert["kind"]): string =>
    themeColor(ALERT_TOKEN[kind] ?? "", ALERT_FALLBACK);

  const map = new maplibregl.Map({
    container: opts.container,
    style: offlineStyle(),
    bounds: WORLD_BOUNDS,
    fitBoundsOptions: { padding: 12, animate: false },
    attributionControl: false,
    interactive: true,
  });

  // ---- overlays ------------------------------------------------------------
  //
  // The SVG carries every fixed-size map shape; the label layer carries every
  // word. Both ignore the pointer except for their own click targets.
  const overlay = h("div", { class: "watch-map-overlay" });
  overlay.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;z-index:1;";

  const labelLayer = h("div", { class: "watch-map-labels" });
  labelLayer.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;";

  const svg = svgEl("svg", { class: "watch-map-svg", "aria-hidden": "true" });
  svg.style.cssText = "position:absolute;left:0;top:0;overflow:visible;pointer-events:none;";

  const focusLineLayer = svgEl("g", { class: "watch-focus-lines" });
  const contactLayer = svgEl("g", { class: "watch-contacts" });
  const alertLayer = svgEl("g", { class: "watch-alert-dots" });
  const fixLayer = svgEl("g", { class: "watch-fixes" });
  svg.append(focusLineLayer, contactLayer, alertLayer, fixLayer);

  overlay.append(labelLayer, svg);

  // ---- standard controls ----------------------------------------------------
  //
  // Zoom and the compass are our own buttons so the control chrome stays flat
  // and hairline-thin instead of inheriting the default rounded white group.
  // The scale bar is MapLibre's ScaleControl, styled by watch.css.
  const controls = h("div", { class: "watch-map-controls" });
  const zoomIn = h(
    "button",
    { class: "watch-map-btn", type: "button", title: "Zoom in", "aria-label": "Zoom in" },
    "+",
  );
  const zoomOut = h(
    "button",
    { class: "watch-map-btn", type: "button", title: "Zoom out", "aria-label": "Zoom out" },
    "\u2212",
  );
  const compass = h(
    "button",
    { class: "watch-map-btn is-compass", type: "button", title: "Reset north", "aria-label": "Reset north" },
    "N",
  );
  zoomIn.addEventListener("click", () => map.zoomIn());
  zoomOut.addEventListener("click", () => map.zoomOut());
  compass.addEventListener("click", () => {
    map.easeTo({
      bearing: 0,
      pitch: 0,
      duration: prefersReducedMotion() ? 0 : 250,
    });
  });
  controls.append(zoomIn, zoomOut, compass);

  const layerState = { vessels: true, events: true, areas: true, reach: true };
  type LayerKey = keyof typeof layerState;

  const layerPanel = h("div", {
    class: "watch-layers",
    role: "group",
    "aria-label": "Map layers",
  });
  for (const [key, label] of [
    ["vessels", "Vessels"],
    ["events", "Events"],
    ["areas", "Areas"],
    ["reach", "Reach"],
  ] as [LayerKey, string][]) {
    const input = h("input", {
      class: "watch-layer-check",
      type: "checkbox",
      checked: layerState[key],
    });
    input.addEventListener("change", () => {
      layerState[key] = input.checked;
      applyLayerVisibility();
    });
    layerPanel.append(h("label", { class: "watch-layer" }, input, h("span", null, label)));
  }

  const cursorEl = h(
    "div",
    { class: "watch-cursor", "aria-hidden": "true" },
    "\u2014, \u2014",
  );

  opts.container.append(overlay, controls, layerPanel, cursorEl);

  map.on("mousemove", (event) => {
    cursorEl.textContent = `${event.lngLat.lat.toFixed(4)}, ${event.lngLat.lng.toFixed(4)}`;
  });
  map.on("mouseout", () => {
    cursorEl.textContent = "\u2014, \u2014";
  });

  let ready = false;
  let destroyed = false;

  let data: WatchResponse | null = null;
  let dots: AlertDot[] = [];
  let areaLabels = new Map<string, HTMLElement>();
  /** Measured once per rebuild: reading offsetWidth every frame would relayout. */
  let areaLabelWidths = new Map<string, number>();
  /** Watched areas first, so their names win a collision. */
  let areaOrder: WatchArea[] = [];

  let focus: WatchAlert | null = null;
  let contactFocus: FocusShip | null = null;
  /** Which alert the focus drawing belongs to, so a refresh keeps the contact ring. */
  let appliedAlertId: string | null = null;

  /** True when the camera is away from the world because one alert is open. */
  let focusing = false;
  let savedCamera: { center: LngLat; zoom: number } | null = null;

  /** SVG pieces of the focus view, re-positioned on every camera move. */
  let lastDot: SVGCircleElement | null = null;
  let lastRing: SVGCircleElement | null = null;
  let headingTick: SVGLineElement | null = null;
  let nextLine: SVGLineElement | null = null;
  let nextDot: SVGCircleElement | null = null;
  let contacts: ContactDot[] = [];
  let contactRing: SVGCircleElement | null = null;
  let contactLabel: HTMLElement | null = null;
  let contactLngLat: LngLat | null = null;

  let raf: number | null = null;
  let ticker: number | null = null;
  let animToken = 0;

  function source(id: string): maplibregl.GeoJSONSource | undefined {
    return map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  }

  /** Layer groups behind the four checkboxes in the top-right panel. */
  const AREA_LAYERS = ["watch-area-fill", "watch-area-watched-edge", "watch-area-listening"];
  const SHIP_LAYERS = ["watch-ship", "watch-ship-watch"];
  const REACH_LAYERS = ["watch-reach-fill", "watch-reach-edge"];

  function applyLayerVisibility(): void {
    if (destroyed) return;
    if (ready) {
      const set = (ids: string[], visible: boolean): void => {
        for (const id of ids) {
          map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
        }
      };
      set(SHIP_LAYERS, layerState.vessels);
      set(AREA_LAYERS, layerState.areas);
      set(REACH_LAYERS, layerState.reach);
    }
    // Alert dots are SVG, not MapLibre layers.
    alertLayer.style.display = layerState.events ? "" : "none";
    positionOverlays();
  }

  function sizeSvg(): void {
    const width = Math.max(0, opts.container.clientWidth);
    const height = Math.max(0, opts.container.clientHeight);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  }

  /** True when a projected point is close enough to the stage to be worth ink. */
  function onScreen(x: number, y: number, margin = 60): boolean {
    return (
      x >= -margin &&
      y >= -margin &&
      x <= opts.container.clientWidth + margin &&
      y <= opts.container.clientHeight + margin
    );
  }

  /** Place an HTML label at a projected point; hide it off screen. */
  function place(
    element: HTMLElement,
    lngLat: LngLat,
    anchor: "centre" | "above" | "beside",
    gap = 10,
  ): void {
    const point = map.project(lngLat);
    if (!onScreen(point.x, point.y)) {
      element.style.visibility = "hidden";
      return;
    }
    element.style.visibility = "visible";
    if (anchor === "above") {
      element.style.transform = `translate(${point.x}px, ${point.y - gap}px) translate(-50%, -100%)`;
    } else if (anchor === "beside") {
      element.style.transform = `translate(${point.x + gap}px, ${point.y}px) translate(0, -50%)`;
    } else {
      element.style.transform = `translate(${point.x}px, ${point.y}px) translate(-50%, -50%)`;
    }
  }

  function drawReachRing(alert: WatchAlert, atMs: number): void {
    if (!ready) return;
    const ring = reachRing(alert, atMs);
    const src = source("watch-reach");
    if (src === undefined) return;
    if (ring === null || ring.length < 3) {
      void src.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    void src.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [ring] },
          properties: {},
        },
      ],
    });
  }

  function stopReachTimers(): void {
    animToken += 1;
    if (raf !== null) {
      window.cancelAnimationFrame(raf);
      raf = null;
    }
    if (ticker !== null) {
      window.clearInterval(ticker);
      ticker = null;
    }
  }

  /**
   * The reach grows from the last fix to the display time over one second. An
   * ongoing alert then keeps growing once a second: the passage of time is the
   * whole point of the screen. Reduced motion gets the final state immediately.
   */
  function animateReach(alert: WatchAlert, animate: boolean): void {
    stopReachTimers();
    const lastT = parseMs(alert.last.t);
    const finalT = reachTimeMs(alert, Date.now());
    if (lastT === null || !animate || prefersReducedMotion()) {
      drawReachRing(alert, finalT);
      if (alert.status === "ongoing") {
        ticker = window.setInterval(() => {
          if (destroyed || focus === null) return;
          drawReachRing(focus, reachTimeMs(focus, Date.now()));
        }, LIVE_STEP_MS);
      }
      return;
    }

    const token = animToken;
    const started = performance.now();
    const step = (now: number): void => {
      if (destroyed || token !== animToken) return;
      const progress = Math.min(1, (now - started) / REACH_MS);
      drawReachRing(alert, lastT + (finalT - lastT) * progress);
      if (progress < 1) {
        raf = window.requestAnimationFrame(step);
        return;
      }
      raf = null;
      if (alert.status === "ongoing") {
        ticker = window.setInterval(() => {
          if (destroyed || focus === null) return;
          drawReachRing(focus, reachTimeMs(focus, Date.now()));
        }, LIVE_STEP_MS);
      }
    };
    raf = window.requestAnimationFrame(step);
  }

  function clearFocusShapes(): void {
    focusLineLayer.replaceChildren();
    contactLayer.replaceChildren();
    fixLayer.replaceChildren();
    // Contact names live in the label layer, not the SVG: remove them with the
    // dots they belong to or every rebuild leaves a stray name at the origin.
    for (const dot of contacts) dot.label?.remove();
    lastDot = null;
    lastRing = null;
    headingTick = null;
    nextLine = null;
    nextDot = null;
    contacts = [];
    contactRing = null;
    if (contactLabel !== null) {
      contactLabel.remove();
      contactLabel = null;
    }
    contactLngLat = null;
  }

  /** Rebuild the alert's own shapes: last fix, heading, next fix, contact dots. */
  function buildFocusShapes(alert: WatchAlert): void {
    clearFocusShapes();

    const color = alertColor(alert.kind);

    // The last fix is the anchor of the whole story: a red dot with the
    // direction the ship was steering.
    lastRing = svgEl("circle", {
      cx: 0,
      cy: 0,
      r: 8,
      fill: "none",
      stroke: color,
      "stroke-opacity": 0.5,
      "stroke-width": 1,
    });
    lastDot = svgEl("circle", {
      cx: 0,
      cy: 0,
      r: 4,
      fill: colors.alert,
      stroke: colors.sea,
      "stroke-width": 1,
    });
    headingTick = svgEl("line", {
      x1: 0,
      y1: 0,
      x2: 0,
      y2: 0,
      stroke: colors.alert,
      "stroke-width": 1.5,
      "stroke-linecap": "round",
    });
    fixLayer.append(lastRing, headingTick, lastDot);

    if (alert.next !== null) {
      nextLine = svgEl("line", {
        x1: 0,
        y1: 0,
        x2: 0,
        y2: 0,
        stroke: rgba(colors.text2, 0.7),
        "stroke-width": 1,
        "stroke-dasharray": "2 4",
      });
      nextDot = svgEl("circle", {
        cx: 0,
        cy: 0,
        r: 4,
        fill: "none",
        stroke: colors.text1,
        "stroke-width": 1,
      });
      focusLineLayer.append(nextLine);
      fixLayer.append(nextDot);
    }

    for (const contact of alert.contacts) {
      const sanctioned = contact.watchlist === "sanctioned";
      const couldMeet = contact.could_meet === true;
      const radius = sanctioned || couldMeet ? 3.5 : 2.2;
      const fill = sanctioned ? colors.alert : couldMeet ? colors.gap : colors.text2;
      const circle = svgEl("circle", {
        cx: 0,
        cy: 0,
        r: radius,
        fill,
        "fill-opacity": sanctioned || couldMeet ? 0.95 : 0.55,
        stroke: colors.sea,
        "stroke-width": 1.5,
      });
      circle.append(
        svgTitle(`${displayName(contact.name, contact.mmsi)} \u00b7 ${Math.round(contact.min_dist_km)} km`),
      );
      contactLayer.append(circle);

      let label: HTMLElement | null = null;
      if (sanctioned) {
        label = h(
          "div",
          { class: "watch-contact-label is-sanctioned" },
          displayName(contact.name, contact.mmsi),
        );
        label.style.borderColor = colors.alert;
        labelLayer.append(label);
      }
      contacts.push({ contact, circle, label });
    }
  }

  function clearContactFocus(): void {
    contactFocus = null;
    if (contactRing !== null) {
      contactRing.remove();
      contactRing = null;
    }
    if (contactLabel !== null) {
      contactLabel.remove();
      contactLabel = null;
    }
    contactLngLat = null;
  }

  function drawContactFocus(): void {
    if (contactRing !== null) contactRing.remove();
    contactRing = null;
    if (contactLabel !== null) contactLabel.remove();
    contactLabel = null;
    contactLngLat = null;

    if (contactFocus === null) return;
    const color = contactFocus.sanctioned ? colors.alert : colors.text1;
    contactLngLat = [contactFocus.lon, contactFocus.lat];
    contactRing = svgEl("circle", {
      cx: 0,
      cy: 0,
      r: 12,
      fill: "none",
      stroke: color,
      "stroke-width": 1.5,
    });
    fixLayer.append(contactRing);
    contactLabel = h(
      "div",
      { class: "watch-contact-label is-focus" },
      displayName(contactFocus.name, contactFocus.mmsi),
    );
    contactLabel.style.borderColor = color;
    labelLayer.append(contactLabel);
  }

  /** Re-project every overlay onto the current camera; cheap, runs per frame. */
  function positionOverlays(): void {
    if (destroyed || !ready) return;

    // Alert dots.
    for (const dot of dots) {
      const point = map.project([dot.alert.last.lon, dot.alert.last.lat]);
      if (!onScreen(point.x, point.y, 20)) {
        dot.group.style.display = "none";
        continue;
      }
      dot.group.style.display = "";
      dot.group.setAttribute("transform", `translate(${point.x} ${point.y})`);
    }

    // Area names: watched first (they carry the alerts). A name that would sit
    // on another is nudged above or below its area before it is dropped: at
    // world zoom the Gulf and the Strait are only pixels apart. Names only
    // appear from zoom 4 in, so the world view stays a field, not a legend.
    const showAreaLabels = layerState.areas && map.getZoom() >= 4;
    const placed: { left: number; top: number; right: number; bottom: number }[] = [];
    for (const area of areaOrder) {
      const label = areaLabels.get(area.id);
      if (label === undefined) continue;
      if (!showAreaLabels) {
        label.style.visibility = "hidden";
        continue;
      }
      const [w, s, e, n] = area.bbox;
      const cx = (w + e) / 2;
      // Watched names sit inside their southern edge, away from the fixes the
      // alerts are drawn on; listening names sit at the area's centre.
      const anchorY = area.kind === "watched" ? s : (s + n) / 2;
      const point = map.project([cx, anchorY]);
      if (!onScreen(point.x, point.y, 10)) {
        label.style.visibility = "hidden";
        continue;
      }
      const centreY = point.y - (area.kind === "watched" ? 10 : 0);
      const halfWidth = (areaLabelWidths.get(area.id) ?? 80) / 2 + 4;
      const halfHeight = 8;
      let chosen: { left: number; top: number; right: number; bottom: number } | null = null;
      let chosenY = centreY;
      for (const shift of [0, -26, 26, -52, 52]) {
        const y = centreY + shift;
        const rect = {
          left: point.x - halfWidth,
          right: point.x + halfWidth,
          top: y - halfHeight,
          bottom: y + halfHeight,
        };
        const crowded = placed.some(
          (other) =>
            rect.left < other.right &&
            rect.right > other.left &&
            rect.top < other.bottom &&
            rect.bottom > other.top,
        );
        if (!crowded) {
          chosen = rect;
          chosenY = y;
          break;
        }
      }
      if (chosen === null) {
        label.style.visibility = "hidden";
        continue;
      }
      placed.push(chosen);
      label.style.visibility = "visible";
      label.style.transform = `translate(${point.x}px, ${chosenY}px) translate(-50%, -50%)`;
    }

    // Focus: the fixes, the heading tick and the contacts.
    if (focus === null) return;

    const last = map.project([focus.last.lon, focus.last.lat]);
    const visibleLast = onScreen(last.x, last.y);

    if (lastDot !== null) {
      lastDot.style.display = visibleLast ? "" : "none";
      lastDot.setAttribute("cx", String(last.x));
      lastDot.setAttribute("cy", String(last.y));
    }
    if (lastRing !== null) {
      lastRing.style.display = visibleLast ? "" : "none";
      lastRing.setAttribute("cx", String(last.x));
      lastRing.setAttribute("cy", String(last.y));
    }
    if (headingTick !== null) {
      const rad = (focus.last.cog * Math.PI) / 180;
      const length = 11;
      headingTick.style.display = visibleLast ? "" : "none";
      headingTick.setAttribute("x1", String(last.x + Math.sin(rad) * 4));
      headingTick.setAttribute("y1", String(last.y - Math.cos(rad) * 4));
      headingTick.setAttribute("x2", String(last.x + Math.sin(rad) * length));
      headingTick.setAttribute("y2", String(last.y - Math.cos(rad) * length));
    }

    if (focus.next !== null && nextDot !== null) {
      const next = map.project([focus.next.lon, focus.next.lat]);
      const visible = onScreen(next.x, next.y);
      nextDot.style.display = visible ? "" : "none";
      nextDot.setAttribute("cx", String(next.x));
      nextDot.setAttribute("cy", String(next.y));
      if (nextLine !== null) {
        nextLine.style.display = visible && visibleLast ? "" : "none";
        nextLine.setAttribute("x1", String(last.x));
        nextLine.setAttribute("y1", String(last.y));
        nextLine.setAttribute("x2", String(next.x));
        nextLine.setAttribute("y2", String(next.y));
      }
    }

    for (const dot of contacts) {
      const point = map.project([dot.contact.lon, dot.contact.lat]);
      const visible = onScreen(point.x, point.y);
      dot.circle.style.display = visible ? "" : "none";
      dot.circle.setAttribute("cx", String(point.x));
      dot.circle.setAttribute("cy", String(point.y));
      if (dot.label !== null) {
        if (visible) place(dot.label, [dot.contact.lon, dot.contact.lat], "beside", 9);
        else dot.label.style.visibility = "hidden";
      }
    }

    if (contactRing !== null && contactLngLat !== null) {
      const point = map.project(contactLngLat);
      const visible = onScreen(point.x, point.y);
      contactRing.style.display = visible ? "" : "none";
      contactRing.setAttribute("cx", String(point.x));
      contactRing.setAttribute("cy", String(point.y));
      if (contactLabel !== null) {
        if (visible) place(contactLabel, contactLngLat, "beside", 15);
        else contactLabel.style.visibility = "hidden";
      }
    }
  }

  /** Lowest priority first, so the worst alert's dot is drawn on top. */
  function alertDrawOrder(alerts: WatchAlert[]): WatchAlert[] {
    return [...alerts].sort((a, b) => {
      const aOngoing = a.status === "ongoing" ? 1 : 0;
      const bOngoing = b.status === "ongoing" ? 1 : 0;
      if (aOngoing !== bOngoing) return aOngoing - bOngoing;
      if (a.severity !== b.severity) return a.severity - b.severity;
      return (parseMs(a.t_start) ?? 0) - (parseMs(b.t_start) ?? 0);
    });
  }

  /** Rebuild the clickable alert dots; five of them, so a full rebuild is fine. */
  function rebuildAlertDots(): void {
    alertLayer.replaceChildren();
    dots = [];
    if (data === null) return;

    for (const alert of alertDrawOrder(data.alerts)) {
      const group = svgEl("g", { class: `watch-alert-dot is-${alert.kind}` });
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "-1");
      group.setAttribute(
        "aria-label",
        `${alert.label}: ${displayName(alert.name, alert.mmsi)}`,
      );
      const color = alertColor(alert.kind);
      // 6 px across, flat fill, no halo: an event marker, not a glow.
      group.append(
        svgEl("circle", {
          cx: 0,
          cy: 0,
          r: 3,
          fill: color,
          stroke: colors.sea,
          "stroke-width": 1,
        }),
        svgEl("circle", { cx: 0, cy: 0, r: 10, class: "watch-alert-hit" }),
      );
      group.addEventListener("click", (clicked) => {
        clicked.stopPropagation();
        if (!destroyed) opts.onSelectAlert(alert.id);
      });
      alertLayer.append(group);
      dots.push({ alert, group });
    }
    positionOverlays();
  }

  function rebuildAreaLabels(): void {
    for (const label of areaLabels.values()) label.remove();
    areaLabels = new Map();
    areaLabelWidths = new Map();
    areaOrder = [...(data?.areas ?? [])].sort((a, b) =>
      a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "watched" ? -1 : 1,
    );
    if (data === null) return;

    for (const area of data.areas) {
      const label = h(
        "div",
        { class: `watch-area-label is-${area.kind}` },
        area.name,
      );
      labelLayer.append(label);
      areaLabels.set(area.id, label);
      areaLabelWidths.set(area.id, label.offsetWidth || 80);
    }
  }

  function setData(next: WatchResponse): void {
    if (destroyed) return;
    data = next;

    if (ready) {
      const areas = source("watch-areas");
      if (areas !== undefined) void areas.setData(areaFeatures(next.areas));
      const ships = source("watch-ships");
      if (ships !== undefined) void ships.setData(shipFeatures(next.ships));
    }

    rebuildAlertDots();
    rebuildAreaLabels();
    positionOverlays();

    // A refresh may replace the alert object; keep the same alert open, with
    // the same camera, and re-read its numbers.
    if (focus !== null) {
      const updated = next.alerts.find((alert) => alert.id === focus?.id) ?? null;
      if (updated === null) {
        showWorld();
      } else {
        applyFocus(updated, { animate: false, moveCamera: false });
      }
    }
  }

  function flyToLast(alert: WatchAlert, moveCamera: boolean): void {
    if (!ready || !moveCamera) return;
    const center: LngLat = [alert.last.lon, alert.last.lat];
    if (prefersReducedMotion()) {
      map.jumpTo({ center, zoom: FOCUS_ZOOM });
      return;
    }
    map.flyTo({ center, zoom: FOCUS_ZOOM, duration: FOCUS_MS, essential: true });
  }

  function applyFocus(
    alert: WatchAlert,
    options: { animate: boolean; moveCamera: boolean },
  ): void {
    if (destroyed) return;
    if (focusing === false && savedCamera === null) {
      const center = map.getCenter();
      savedCamera = { center: [center.lng, center.lat], zoom: map.getZoom() };
    }
    focusing = true;
    focus = alert;

    // A contact ring belongs to the alert it was opened in; a new alert starts
    // clean, a refresh of the same alert keeps it.
    if (appliedAlertId !== alert.id) {
      clearContactFocus();
      appliedAlertId = alert.id;
    }

    // Selection ring on the alert dot, and the selected dot above its
    // neighbours: three of these alerts can sit within a few pixels at world
    // zoom, and the one being read must be the one you see.
    for (const dot of dots) {
      const selected = dot.alert.id === alert.id;
      dot.group.classList.toggle("is-selected", selected);
      if (selected) alertLayer.append(dot.group);
    }

    buildFocusShapes(alert);
    drawContactFocus();
    positionOverlays();
    flyToLast(alert, options.moveCamera);
    animateReach(alert, options.animate);
  }

  function showWorld(): void {
    if (destroyed) return;
    stopReachTimers();
    focus = null;
    focusing = false;
    appliedAlertId = null;
    clearFocusShapes();
    clearContactFocus();
    if (ready) {
      const src = source("watch-reach");
      if (src !== undefined) void src.setData({ type: "FeatureCollection", features: [] });
    }
    for (const dot of dots) dot.group.classList.remove("is-selected");

    if (savedCamera !== null && ready) {
      const camera = savedCamera;
      savedCamera = null;
      if (prefersReducedMotion()) {
        map.jumpTo({ center: camera.center, zoom: camera.zoom });
      } else {
        map.flyTo({
          center: camera.center,
          zoom: camera.zoom,
          duration: 900,
          essential: true,
        });
      }
    }
    positionOverlays();
  }

  map.on("load", () => {
    if (destroyed) return;

    map.addSource("watch-areas", {
      type: "geojson",
      data: areaFeatures(data?.areas ?? []),
    });
    map.addSource("watch-reach", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    map.addSource("watch-ships", {
      type: "geojson",
      data: shipFeatures(data?.ships ?? []),
    });

    // Watched areas: a thin solid outline, no fill at world zoom (a very faint
    // wash only once the camera is close enough for a bbox to mean an area).
    map.addLayer({
      id: "watch-area-fill",
      type: "fill",
      source: "watch-areas",
      filter: ["==", ["get", "kind"], "watched"],
      paint: {
        "fill-color": colors.lineStrong,
        "fill-opacity": ["step", ["zoom"], 0, 4, 0.06],
      },
    });
    map.addLayer({
      id: "watch-area-watched-edge",
      type: "line",
      source: "watch-areas",
      filter: ["==", ["get", "kind"], "watched"],
      paint: {
        "line-color": colors.text3,
        "line-opacity": 0.65,
        "line-width": 1,
      },
    });
    // Listening areas are the thin dashed line only.
    map.addLayer({
      id: "watch-area-listening",
      type: "line",
      source: "watch-areas",
      filter: ["==", ["get", "kind"], "listening"],
      paint: {
        "line-color": colors.text3,
        "line-opacity": 0.5,
        "line-width": 1,
        "line-dasharray": [3, 3],
      },
    });

    // The reach area: the prism colour, because it is a possibility rather
    // than a finding, and dashed for the same reason. Flat, 1 px edge.
    map.addLayer({
      id: "watch-reach-fill",
      type: "fill",
      source: "watch-reach",
      paint: {
        "fill-color": colors.prism,
        "fill-opacity": 0.08,
      },
    });
    map.addLayer({
      id: "watch-reach-edge",
      type: "line",
      source: "watch-reach",
      paint: {
        "line-color": colors.prism,
        "line-opacity": 0.85,
        "line-width": 1,
        "line-dasharray": [3, 3],
      },
    });

    // Every ship: 1.5 px across, muted; watchlisted hulls only a little brighter.
    map.addLayer({
      id: "watch-ship",
      type: "circle",
      source: "watch-ships",
      filter: ["==", ["get", "watch"], 0],
      paint: {
        "circle-radius": 0.75,
        "circle-color": colors.text2,
        "circle-opacity": 0.45,
      },
    });
    map.addLayer({
      id: "watch-ship-watch",
      type: "circle",
      source: "watch-ships",
      filter: ["==", ["get", "watch"], 1],
      paint: {
        "circle-radius": 1.1,
        "circle-color": colors.text1,
        "circle-opacity": 0.65,
      },
    });

    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }), "bottom-left");

    ready = true;
    sizeSvg();
    rebuildAlertDots();
    rebuildAreaLabels();
    applyLayerVisibility();
    positionOverlays();

    // A focus applied before the first frame (deep link) owns the camera now;
    // otherwise the camera would stay on the world fit.
    if (focus !== null) {
      const alert = focus;
      flyToLast(alert, true);
      animateReach(alert, false);
    }
  });

  map.on("render", positionOverlays);
  map.on("move", positionOverlays);

  return {
    setData,

    focusAlert(alert: WatchAlert, options?: { animate?: boolean; moveCamera?: boolean }): void {
      applyFocus(alert, {
        animate: options?.animate ?? true,
        moveCamera: options?.moveCamera ?? true,
      });
    },

    focusContact(ship: FocusShip | null): void {
      if (destroyed) return;
      if (ship === null) {
        clearContactFocus();
        positionOverlays();
        return;
      }
      contactFocus = ship;
      drawContactFocus();
      positionOverlays();
      if (!ready) return;
      const center: LngLat = [ship.lon, ship.lat];
      if (prefersReducedMotion()) {
        map.jumpTo({ center, zoom: 9 });
      } else {
        map.flyTo({ center, zoom: 9, duration: 700, essential: true });
      }
    },

    showWorld,

    resize(): void {
      if (destroyed) return;
      map.resize();
      sizeSvg();
      positionOverlays();
    },

    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      stopReachTimers();
      overlay.remove();
      controls.remove();
      layerPanel.remove();
      cursorEl.remove();
      map.remove();
    },
  };
}

/** The attribution line the world view shows under the map. */
export function watchAttribution(): HTMLElement {
  return h(
    "div",
    { class: "watch-attrib" },
    "Positions: our AIS recording \u00b7 Watchlist: ",
    h(
      "a",
      {
        href: "https://www.opensanctions.org",
        target: "_blank",
        rel: "noopener noreferrer",
      },
      "OpenSanctions",
    ),
  );
}
