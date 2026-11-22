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

import type {
  VesselTrackPoint,
  WatchAlert,
  WatchArea,
  WatchContact,
  WatchResponse,
  WatchShip,
} from "../api";
import type { LivePosition } from "../model/liveFeed";
import { offlineStyle } from "../basemap";
import { h } from "../components/dom";
import { fmtInt } from "../format";
import { rgba, themeColor } from "./theme";

type LngLat = [number, number];
type Bounds = [[number, number], [number, number]];

/**
 * Where the screen opens: the Baltic, which is where most ships are heard.
 * The empty world is not a first view; Esc and "All areas" come back here.
 */
const WORLD_BOUNDS: Bounds = [
  [9.5, 53.5],
  [30.5, 66],
];

/** Focus camera: close enough that a 60 km reach area is a shape, not a speck. */
const FOCUS_ZOOM = 8;
const FOCUS_MS = 900;
/** Every camera move on this screen: one duration, one easing (MapLibre default). */
const MOVE_MS = 900;
/** Live positions glide to their new fix over this long, redrawn at most 10 times a second. */
const GLIDE_MS = 1000;
const GLIDE_TICK_MS = 100;
/** At most this many pulses per batch of positions, so a busy region stays calm. */
const MAX_PULSES = 24;

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
  dark: "--st-dark",
  spoof: "--st-spoof",
  jump: "--st-spoof",
  meeting: "--text-1",
  sanctioned_stop: "--st-sanctioned",
  radar_unmatched: "--st-radar",
};

/** Fallback when an alert kind is not one this build knows. */
const ALERT_FALLBACK = "#F26457";

/**
 * A hull under way with a known course (speed over 0.5 kn, a numeric course):
 * drawn as a chevron, every other ship as a dot, so none is drawn twice.
 */
const MOVING_FILTER: maplibregl.ExpressionSpecification = ["all",
  [">=", ["to-number", ["get", "sog"], 0], 0.5],
  ["==", ["typeof", ["get", "cog"]], "number"],
];

export interface FocusShip {
  mmsi: number;
  name: string | null;
  lat: number;
  lon: number;
  /** Sanctions hit: the ring and the name take the one warning colour. */
  sanctioned: boolean;
}

/** One detection from the satellite check: a radar echo under the alert. */
export interface SatellitePoint {
  lat: number;
  lon: number;
  /** False when our own AIS did not hear a ship at this spot. */
  matched: boolean;
}

/** One radar view: what the Hormuz panel asks the map to draw. */
export interface RadarMapView {
  /** Unmatched radar detections per 0.1 degree cell, aggregated over the whole period. */
  cells: { lat: number; lon: number; n: number }[];
  encounters: { lat: number; lon: number; label: string }[];
  hotspots: { lat: number; lon: number; label: string }[];
  /** Index into `encounters` drawn with the selection ring, or null. */
  selectedEncounter: number | null;
}

export interface WatchMapOptions {
  container: HTMLElement;
  /** A watched area was clicked (its outline, its marker or anywhere inside it). */
  onSelectArea?: (id: string) => void;
  /** An encounter or hotspot marker was clicked: index into the radar view. */
  onSelectMarker?: (kind: "encounter" | "hotspot", index: number) => void;
  /** An alert dot was clicked; the screen decides what that means for the URL. */
  onSelectAlert: (id: string) => void;
  /** A ship dot was clicked; the screen opens that hull's dossier (#182). */
  onSelectVessel?: (mmsi: number) => void;
  /** Where the camera opens (the first alert's area); null or absent opens on the world. */
  opening?: [number, number, number, number] | null;
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
  /**
   * Show an area or a region: clear any alert focus, fit the box, outline the
   * area and ring the selected alert dot. Used by area clicks and the radar view.
   */
  showRegion(opts: { bounds: [number, number, number, number]; areaId: string | null; alertId: string | null }): void;
  /** Draw (or clear with null) the radar density, encounters and hotspots. */
  setRadar(view: RadarMapView | null): void;
  /**
   * Draw the satellite check's detections as fixed-size dots: red where our AIS
   * did not hear a ship, grey where it did. null clears them.
   */
  showSatelliteCheck(points: SatellitePoint[] | null): void;
  /** 0..1 scale on the radar density, for the time-lapse. */
  setRadarIntensity(k: number): void;
  /** Glide dots to new fixes from the live feed and pulse the ones in view. */
  applyLive(items: LivePosition[]): void;
  /** Move the camera to an area, or back to the opening view with null. */
  fitArea(bbox: [number, number, number, number] | null): void;
  /** Draw one hull's 12 h track for the dossier, or clear it with null (#182). */
  showDossierTrack(track: readonly VesselTrackPoint[] | null): void;
  /** [search #183] center on one hull with no alert open: the top-bar search hook. */
  flyToVessel(lat: number, lon: number): void;
  /** #254: hide the focus view's final reach while the replay draws its own. */
  setReachVisible(visible: boolean): void;
  /** Back to the world: clear every focus drawing and restore the camera. */
  showWorld(): void;
  /** #185: AlertReplay owns its own sources on this map. */
  replayMap: maplibregl.Map;
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
export function distanceKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
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
  // A false position has no reach, and a radar alert names no hull.
  if (alert.kind === "spoof" || alert.kind === "radar_unmatched") return null;
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
  sanctioned: number;
  sog: number | null;
  cog: number | null;
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
        sanctioned: ship.watchlist === "sanctioned" ? 1 : 0,
        sog: ship.sog ?? null,
        cog: (ship as { cog?: number | null }).cog ?? null,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

type ShipStoreEntry = {
  lon: number; lat: number; watch: number;
  sanctioned?: number; sog?: number | null; cog?: number | null;
};

function storeFeatures(
  store: Map<number, ShipStoreEntry>,
): GeoJSON.FeatureCollection<GeoJSON.Point, ShipProps> {
  const features: GeoJSON.Feature<GeoJSON.Point, ShipProps>[] = [];
  for (const [mmsi, ship] of store) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [ship.lon, ship.lat] },
      properties: { mmsi, watch: ship.watch, sanctioned: ship.sanctioned ?? 0, sog: ship.sog ?? null, cog: ship.cog ?? null },
    });
  }
  return { type: "FeatureCollection", features };
}

/** A graticule every 10 degrees: faint, so the sea reads as a measured surface. */
function graticule(): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  const features: GeoJSON.Feature<GeoJSON.LineString>[] = [];
  for (let lon = -180; lon <= 180; lon += 10) {
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[lon, -80], [lon, 80]] },
      properties: {},
    });
  }
  for (let lat = -80; lat <= 80; lat += 10) {
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-180, lat], [180, lat]] },
      properties: {},
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

/** A label's screen box, for the collision test before it is drawn. */
interface LabelRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
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
    ais: themeColor("--geo-vessel", "#8797A9"),
    alert: themeColor("--st-sanctioned", "#F26457"),
    gap: themeColor("--st-dark", "#F0A33A"),
    prism: themeColor("--geo-reach", "#C9D6E3"),
    radar: themeColor("--st-radar", "#56B3EA"),
    track: themeColor("--geo-track", "#A9B6C3"),
  };

  const alertColor = (kind: WatchAlert["kind"]): string =>
    themeColor(ALERT_TOKEN[kind] ?? "", ALERT_FALLBACK);

  const map = new maplibregl.Map({
    container: opts.container,
    style: offlineStyle(),
    bounds: opts.opening ? [[opts.opening[0], opts.opening[1]], [opts.opening[2], opts.opening[3]]] : WORLD_BOUNDS,
    fitBoundsOptions: { padding: opts.opening ? 48 : 12, animate: false },
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
  const satelliteLayer = svgEl("g", { class: "watch-satcheck" });
  const alertLayer = svgEl("g", { class: "watch-alert-dots" });
  const fixLayer = svgEl("g", { class: "watch-fixes" });
  const pulseLayer = svgEl("g", { class: "watch-pulses" });
  const areaMarkLayer = svgEl("g", { class: "watch-area-marks" });
  const radarMarkLayer = svgEl("g", { class: "watch-radar-marks" });
  svg.append(
    focusLineLayer,
    pulseLayer,
    areaMarkLayer,
    radarMarkLayer,
    contactLayer,
    satelliteLayer,
    alertLayer,
    fixLayer,
  );

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

  // The legend doubles as the layer switch: each row is the map symbol it names.
  // Clicking a row hides that layer; a hidden row is dimmed, not removed.
  const LEGEND_GLYPH: Record<LayerKey, string> = {
    vessels: '<path d="M7 2L11 12L7 9.5L3 12Z" fill="currentColor"/>',
    events: '<circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="20 6"/>',
    areas: '<rect x="2" y="3" width="10" height="8" fill="none" stroke="currentColor" stroke-dasharray="3 2"/>',
    reach: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor"/>',
  };
  const layerPanel = h("div", { class: "watch-layers", role: "group", "aria-label": "Map legend" });
  // Layer switches sit behind one closed row so the key stays small over the map.
  const layerRows = h("details", { class: "watch-layer-toggles" }, h("summary", null, "Layers"));
  for (const [key, label] of [
    ["vessels", "Ships"],
    ["events", "Alerts"],
    ["areas", "Watched areas"],
    ["reach", "Where it could have gone"],
  ] as [LayerKey, string][]) {
    const glyph = h("span", { class: "watch-layer-glyph", "aria-hidden": "true" });
    glyph.innerHTML = `<svg viewBox="0 0 14 14" width="14" height="14">${LEGEND_GLYPH[key]}</svg>`;
    const row = h(
      "button",
      { class: "watch-layer", type: "button", "aria-pressed": "true" },
      glyph,
      h("span", null, label),
    );
    row.addEventListener("click", () => {
      layerState[key] = !layerState[key];
      row.setAttribute("aria-pressed", layerState[key] ? "true" : "false");
      applyLayerVisibility();
    });
    layerRows.append(row);
  }

  // The key says what the marks mean; the rows above switch the layers, this
  // block only names the colours (#270).
  layerPanel.append(
    h(
      "div",
      { class: "watch-marks-key" },
            h(
        "div",
        { class: "watch-marks-row" },
        h("span", { class: "watch-marks-swatch is-red-label" }),
        "Could meet \u00b7 sanctioned",
      ),
      h(
        "div",
        { class: "watch-marks-row" },
        h("span", { class: "watch-marks-swatch is-orange-label" }),
        "Could meet",
      ),
      h(
        "div",
        { class: "watch-marks-row" },
        h("span", { class: "watch-marks-swatch is-orange-dot" }),
        "Also switched off",
      ),
      h(
        "div",
        { class: "watch-marks-row" },
        h("span", { class: "watch-marks-swatch is-grey-dot" }),
        "Broadcasting",
      ),
    ),
    layerRows,
  );

  const cursorEl = h(
    "div",
    { class: "watch-cursor", "aria-hidden": "true" },
    "\u2014, \u2014",
  );
  cursorEl.style.display = "none";

  opts.container.append(overlay, controls, layerPanel, cursorEl);

  map.on("mousemove", (event) => {
    cursorEl.style.display = "";
    cursorEl.textContent = `${event.lngLat.lat.toFixed(4)}, ${event.lngLat.lng.toFixed(4)}`;
  });
  map.on("mouseout", () => {
    cursorEl.style.display = "none";
  });

  let ready = false;
  let destroyed = false;

  let data: WatchResponse | null = null;
  /** Dossier hook (#182): the open hull's 12 h track, kept through snapshot refreshes. */
  let dossierTrack: VesselTrackPoint[] = [];
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
  /** Spoof: one line per ship, from where it is to the false point. */
  let spoofLines: { line: SVGLineElement; from: LngLat }[] = [];
  /** Satellite check dots, positioned per frame like every other fixed-size mark. */
  let satelliteDots: { lngLat: LngLat; circle: SVGCircleElement }[] = [];
  /** Last drawn detections, so an unchanged refresh does not rebuild the dots. */
  let satelliteSignature: string | null = null;

  let raf: number | null = null;
  let ticker: number | null = null;
  let animToken = 0;

  // Live dots: the store is the one truth the ship layer is drawn from, so a
  // snapshot refresh and a streamed fix cannot disagree about where a hull is.
  type StoredShip = ShipStoreEntry;
  interface Glide {
    lon0: number;
    lat0: number;
    lon1: number;
    lat1: number;
    t0: number;
  }
  let shipStore = new Map<number, StoredShip>();
  const glides = new Map<number, Glide>();
  let glideTimer: number | null = null;

  // Radar view and its markers.
  let radarMarks: { lngLat: LngLat; group: SVGGElement }[] = [];
  let radarView: RadarMapView | null = null;
  let areaMarks: { area: WatchArea; group: SVGGElement }[] = [];
  let selectedAlertId: string | null = null;
  /** A watched area opened through showRegion; its outline is drawn only then. */
  let selectedAreaId: string | null = null;
  /** #254: the replay owns the reach while it plays; the static circle waits. */
  let replayHidesReach = false;
  /** A region asked for before the style finished loading. */
  let pendingRegion: { bounds: [number, number, number, number]; areaId: string | null } | null = null;
  let radarIntensity = 1;

  function source(id: string): maplibregl.GeoJSONSource | undefined {
    return map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  }

  /** Layer groups behind the four checkboxes in the top-right panel. */
  const WATCHED_EDGE_LAYERS = ["watch-area-watched-edge"];
  const SHIP_LAYERS = ["watch-ship", "watch-ship-watch", "watch-ship-chevron", "watch-ship-hit"];
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
      set(["watch-area-fill", "watch-area-listening"], layerState.areas);
      // #254: a watched outline is only meaningful once an area is opened; at world
      // zoom the boxes drew long straight lines across the sea.
      set(WATCHED_EDGE_LAYERS, layerState.areas && selectedAreaId !== null);
      set(REACH_LAYERS, layerState.reach && !replayHidesReach);
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

  /**
   * Place a label beside its point only when it does not touch one already
   * placed (#254). A crowded label is hidden; a later one may still fit.
   */
  function placeBeside(
    element: HTMLElement,
    lngLat: LngLat,
    gap: number,
    placed: LabelRect[],
  ): void {
    const point = map.project(lngLat);
    if (!onScreen(point.x, point.y)) {
      element.style.visibility = "hidden";
      return;
    }
    const width = element.offsetWidth || 64;
    const height = element.offsetHeight || 16;
    const rect: LabelRect = {
      left: point.x + gap,
      top: point.y - height / 2,
      right: point.x + gap + width,
      bottom: point.y + height / 2,
    };
    const crowded = placed.some(
      (other) =>
        rect.left < other.right &&
        rect.right > other.left &&
        rect.top < other.bottom &&
        rect.bottom > other.top,
    );
    if (crowded) {
      element.style.visibility = "hidden";
      return;
    }
    placed.push(rect);
    element.style.visibility = "visible";
    element.style.transform = `translate(${point.x + gap}px, ${point.y}px) translate(0, -50%)`;
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
    spoofLines = [];
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

    if (alert.kind === "spoof") {
      // Every ship that reported the false point: a line from where it is to the point.
      for (const contact of alert.contacts) {
        if (distanceKm(contact.lat, contact.lon, alert.last.lat, alert.last.lon) < 2) continue;
        const line = svgEl("line", {
          x1: 0,
          y1: 0,
          x2: 0,
          y2: 0,
          stroke: colors.prism,
          "stroke-width": 1.25,
          "stroke-dasharray": "5 3",
          "stroke-opacity": 0.9,
        });
        line.append(svgTitle(`${displayName(contact.name, contact.mmsi)} \u00b7 ${fmtInt(Math.round(contact.min_dist_km))} km from the false point`));
        focusLineLayer.append(line);
        spoofLines.push({ line, from: [contact.lon, contact.lat] });
      }
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
        svgTitle(`${displayName(contact.name, contact.mmsi)} \u00b7 ${fmtInt(Math.round(contact.min_dist_km))} km`),
      );
      contactLayer.append(circle);

      // #254: only a hull inside the reach gets a name; the map is not a
      // floating list of every ship the snapshot knows about.
      let label: HTMLElement | null = null;
      if (couldMeet) {
        label = h(
          "div",
          { class: `watch-contact-label${sanctioned ? " is-sanctioned" : " is-meet"}` },
          displayName(contact.name, contact.mmsi),
        );
        label.style.borderColor = sanctioned ? colors.alert : colors.gap;
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

    // Satellite check detections: a fixed 7 px dot, red or grey, no labels.
    for (const mark of satelliteDots) {
      const point = map.project(mark.lngLat);
      if (!onScreen(point.x, point.y, 20)) {
        mark.circle.style.display = "none";
        continue;
      }
      mark.circle.style.display = "";
      mark.circle.setAttribute("cx", String(point.x));
      mark.circle.setAttribute("cy", String(point.y));
    }

    // Watched-area markers: only while the area itself is a speck; closer in the
    // outline is the target.
    const showMarks = layerState.areas && map.getZoom() < 6.5;
    for (const mark of areaMarks) {
      const [w, s, e, n] = mark.area.bbox;
      const point = map.project([(w + e) / 2, (s + n) / 2]);
      if (!showMarks || !onScreen(point.x, point.y, 20)) {
        mark.group.style.display = "none";
        continue;
      }
      mark.group.style.display = "";
      mark.group.setAttribute("transform", `translate(${point.x} ${point.y})`);
    }
    for (const mark of radarMarks) {
      const point = map.project(mark.lngLat);
      if (!onScreen(point.x, point.y, 20)) {
        mark.group.style.display = "none";
        continue;
      }
      mark.group.style.display = "";
      mark.group.setAttribute("transform", `translate(${point.x} ${point.y})`);
    }

    // Area names: watched first (they carry the alerts). A name that would sit
    // on another is nudged above or below its area before it is dropped: at
    // world zoom the Gulf and the Strait are only pixels apart. Names only
    // appear from zoom 4 in, so the world view stays a field, not a legend.
    const showAreaLabels = layerState.areas && map.getZoom() >= 4;
    const placed: LabelRect[] = [];
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
      let chosen: LabelRect | null = null;
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

    for (const spoof of spoofLines) {
      const from = map.project(spoof.from);
      spoof.line.setAttribute("x1", String(from.x));
      spoof.line.setAttribute("y1", String(from.y));
      spoof.line.setAttribute("x2", String(last.x));
      spoof.line.setAttribute("y2", String(last.y));
    }

    // The one contact the table put a ring on keeps its name first (#254);
    // the rest take a free spot or stay quiet rather than pile up.
    if (contactRing !== null && contactLngLat !== null) {
      const point = map.project(contactLngLat);
      const visible = onScreen(point.x, point.y);
      contactRing.style.display = visible ? "" : "none";
      contactRing.setAttribute("cx", String(point.x));
      contactRing.setAttribute("cy", String(point.y));
      if (contactLabel !== null) {
        if (visible) placeBeside(contactLabel, contactLngLat, 15, placed);
        else contactLabel.style.visibility = "hidden";
      }
    }

    for (const dot of contacts) {
      const point = map.project([dot.contact.lon, dot.contact.lat]);
      const visible = onScreen(point.x, point.y);
      dot.circle.style.display = visible ? "" : "none";
      dot.circle.setAttribute("cx", String(point.x));
      dot.circle.setAttribute("cy", String(point.y));
      if (dot.label !== null) {
        if (visible) placeBeside(dot.label, [dot.contact.lon, dot.contact.lat], 9, placed);
        else dot.label.style.visibility = "hidden";
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

  // ---- live positions ------------------------------------------------------

  function pushShips(): void {
    const ships = source("watch-ships");
    if (ships !== undefined) void ships.setData(storeFeatures(shipStore));
  }

  function addPulse(lngLat: LngLat): void {
    const point = map.project(lngLat);
    if (!onScreen(point.x, point.y, 0)) return;
    const group = svgEl("g", { transform: `translate(${point.x} ${point.y})` });
    const ring = svgEl("circle", { cx: 0, cy: 0, r: 4, class: "watch-pulse" });
    group.append(ring);
    pulseLayer.append(group);
    ring.addEventListener("animationend", () => group.remove());
    window.setTimeout(() => group.remove(), 1600);
  }

  function glideStep(): void {
    if (destroyed) return;
    const now = performance.now();
    for (const [mmsi, glide] of glides) {
      const ship = shipStore.get(mmsi);
      if (ship === undefined) {
        glides.delete(mmsi);
        continue;
      }
      const p = Math.min(1, (now - glide.t0) / GLIDE_MS);
      const e = p * (2 - p); // ease-out: arrive smoothly
      ship.lon = glide.lon0 + (glide.lon1 - glide.lon0) * e;
      ship.lat = glide.lat0 + (glide.lat1 - glide.lat0) * e;
      if (p >= 1) glides.delete(mmsi);
    }
    if (ready) pushShips();
    if (glides.size === 0 && glideTimer !== null) {
      window.clearInterval(glideTimer);
      glideTimer = null;
    }
  }

  function applyLive(items: LivePosition[]): void {
    if (destroyed || !ready) return;
    const now = performance.now();
    const reduced = prefersReducedMotion();
    let pulses = 0;
    for (const item of items) {
      const known = shipStore.get(item.mmsi);
      if (known === undefined) {
        shipStore.set(item.mmsi, { lon: item.lon, lat: item.lat, watch: 0, sog: item.sog ?? null });
      } else if (reduced) {
        known.lon = item.lon;
        known.lat = item.lat;
      } else {
        glides.set(item.mmsi, {
          lon0: known.lon,
          lat0: known.lat,
          lon1: item.lon,
          lat1: item.lat,
          t0: now,
        });
      }
      if (pulses < MAX_PULSES && !reduced && layerState.vessels) {
        pulses += 1;
        addPulse([item.lon, item.lat]);
      }
    }
    if (glides.size > 0 && glideTimer === null) {
      glideTimer = window.setInterval(glideStep, GLIDE_TICK_MS);
    } else if (glides.size === 0) {
      pushShips();
    }
  }

  // ---- camera --------------------------------------------------------------

  function rememberCamera(): void {
    if (savedCamera === null) {
      const center = map.getCenter();
      savedCamera = { center: [center.lng, center.lat], zoom: map.getZoom() };
    }
  }

  function fitPadding(): maplibregl.PaddingOptions {
    // On a phone the detail panel is a sheet over the bottom of the map.
    const phone = window.matchMedia("(max-width: 760px)").matches;
    const h = opts.container.clientHeight;
    return phone
      ? { top: 36, left: 24, right: 24, bottom: Math.round(h * 0.42) }
      : { top: 56, left: 56, right: 56, bottom: 56 };
  }

  function fitBox(bounds: Bounds, maxZoom: number): void {
    if (!ready) return;
    rememberCamera();
    map.resize();
    map.fitBounds(bounds, {
      padding: fitPadding(),
      maxZoom,
      duration: prefersReducedMotion() ? 0 : MOVE_MS,
      essential: true,
    });
  }

  function boundsOf(points: LngLat[]): Bounds | null {
    if (points.length === 0) return null;
    let w = Infinity;
    let s = Infinity;
    let e = -Infinity;
    let n = -Infinity;
    for (const [lon, lat] of points) {
      w = Math.min(w, lon);
      e = Math.max(e, lon);
      s = Math.min(s, lat);
      n = Math.max(n, lat);
    }
    if (e - w < 0.02 && n - s < 0.02) return null;
    return [
      [w, s],
      [e, n],
    ];
  }

  // ---- trail ----------------------------------------------------------------

  function trailParts(alert: WatchAlert): { pre: LngLat[]; post: LngLat[] } {
    const pre: LngLat[] = [];
    const post: LngLat[] = [];
    const lastT = parseMs(alert.last.t) ?? Infinity;
    for (const point of alert.trail ?? []) {
      const [lon, lat, t] = point;
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
      if ((parseMs(t) ?? 0) <= lastT + 1000) pre.push([lon, lat]);
      else post.push([lon, lat]);
    }
    // The line ends exactly on the last fix the alert starts from.
    if (pre.length > 0) pre.push([alert.last.lon, alert.last.lat]);
    return { pre, post };
  }

  function drawTrail(alert: WatchAlert | null): void {
    if (!ready) return;
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    const line = (coords: LngLat[]): GeoJSON.FeatureCollection =>
      coords.length < 2
        ? empty
        : {
            type: "FeatureCollection",
            features: [
              { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} },
            ],
          };
    if (alert === null) {
      void source("watch-trail")?.setData(empty);
      void source("watch-trail-after")?.setData(empty);
      return;
    }
    const { pre, post } = trailParts(alert);
    void source("watch-trail")?.setData(line(pre));
    void source("watch-trail-after")?.setData(line(post));
  }

  /** Fit the reach area (and the fixes that bound it) in view; fall back to a fixed zoom. */
  function flyToFocus(alert: WatchAlert): void {
    if (!ready) return;
    const points: LngLat[] = [[alert.last.lon, alert.last.lat]];
    if (alert.next !== null) points.push([alert.next.lon, alert.next.lat]);
    const ring = reachRing(alert, reachTimeMs(alert, Date.now()));
    if (ring !== null) points.push(...ring);
    else points.push(...trailParts(alert).pre);
    if (alert.kind === "spoof") {
      for (const contact of alert.contacts) points.push([contact.lon, contact.lat]);
    }
    const bounds = boundsOf(points);
    if (bounds !== null) {
      // A stopped ship's 1 km check circle fills the screen at zoom 11 and shows open sea
      // only; zoom 9 keeps the coast and nearby traffic in view.
      fitBox(bounds, alert.kind === "sanctioned_stop" ? 9 : 11);
      return;
    }
    rememberCamera();
    const center: LngLat = [alert.last.lon, alert.last.lat];
    if (prefersReducedMotion()) map.jumpTo({ center, zoom: FOCUS_ZOOM });
    else map.flyTo({ center, zoom: FOCUS_ZOOM, duration: MOVE_MS, essential: true });
  }

  // ---- radar + areas ----------------------------------------------------------

  function cellFeatures(view: RadarMapView): GeoJSON.FeatureCollection<GeoJSON.Polygon> {
    const maxN = Math.max(1, ...view.cells.map((cell) => cell.n));
    return {
      type: "FeatureCollection",
      features: view.cells.map((cell) => {
        const half = 0.05;
        return {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [cell.lon - half, cell.lat - half],
                [cell.lon + half, cell.lat - half],
                [cell.lon + half, cell.lat + half],
                [cell.lon - half, cell.lat + half],
                [cell.lon - half, cell.lat - half],
              ],
            ],
          },
          properties: { n: cell.n, w: Math.sqrt(cell.n / maxN) },
        };
      }),
    };
  }

  function applyRadarIntensity(): void {
    if (!ready || map.getLayer("watch-radar-fill") === undefined) return;
    map.setPaintProperty("watch-radar-fill", "fill-opacity", [
      "*",
      radarIntensity,
      ["interpolate", ["linear"], ["get", "w"], 0, 0.14, 1, 0.9],
    ]);
  }

  function buildRadarMarks(): void {
    radarMarkLayer.replaceChildren();
    radarMarks = [];
    if (radarView === null) return;
    const make = (
      kind: "encounter" | "hotspot",
      index: number,
      lngLat: LngLat,
      label: string,
      selected: boolean,
    ): void => {
      const group = svgEl("g", { class: `watch-radar-mark is-${kind}${selected ? " is-selected" : ""}` });
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "-1");
      group.setAttribute("aria-label", label);
      group.append(svgTitle(label));
      if (kind === "encounter") {
        // Two small rings: two hulls that met.
        group.append(
          svgEl("circle", { cx: -2, cy: 0, r: 3, class: "watch-enc-ring", fill: "none" }),
          svgEl("circle", { cx: 2, cy: 0, r: 3, class: "watch-enc-ring", fill: "none" }),
        );
      } else {
        group.append(
          svgEl("rect", {
            x: -3.5,
            y: -3.5,
            width: 7,
            height: 7,
            class: "watch-hot-mark",
            fill: "none",
            transform: "rotate(45)",
          }),
        );
      }
      group.append(svgEl("circle", { cx: 0, cy: 0, r: 10, class: "watch-alert-hit" }));
      group.addEventListener("click", (clicked) => {
        clicked.stopPropagation();
        if (!destroyed) opts.onSelectMarker?.(kind, index);
      });
      radarMarkLayer.append(group);
      radarMarks.push({ lngLat, group });
    };
    radarView.encounters.forEach((enc, i) =>
      make("encounter", i, [enc.lon, enc.lat], enc.label, radarView?.selectedEncounter === i),
    );
    radarView.hotspots.forEach((hot, i) => make("hotspot", i, [hot.lon, hot.lat], hot.label, false));
  }

  function setRadar(view: RadarMapView | null): void {
    if (destroyed) return;
    radarView = view;
    if (ready) {
      void source("watch-radar")?.setData(
        view === null ? { type: "FeatureCollection", features: [] } : cellFeatures(view),
      );
    }
    buildRadarMarks();
    positionOverlays();
  }

  /**
   * The satellite check's detections (issue #268). Red is a ship radar saw and
   * our AIS did not hear; grey is one both saw. A 7 px dot each. Coordinates
   * are the only state: an unchanged list is not redrawn, so a snapshot
   * refresh costs nothing.
   */
  function showSatelliteCheck(points: SatellitePoint[] | null): void {
    if (destroyed) return;
    const next = points ?? [];
    const signature = next
      .map((point) => `${point.lat},${point.lon},${point.matched ? 1 : 0}`)
      .join(";");
    if (signature === satelliteSignature) return;
    satelliteSignature = signature;

    satelliteLayer.replaceChildren();
    satelliteDots = [];
    for (const point of next) {
      if (!Number.isFinite(point.lat) || !Number.isFinite(point.lon)) continue;
      const circle = svgEl("circle", {
        cx: 0,
        cy: 0,
        r: 3.5,
        fill: point.matched ? colors.ais : colors.alert,
        stroke: colors.sea,
        "stroke-width": 1,
      });
      satelliteLayer.append(circle);
      satelliteDots.push({ lngLat: [point.lon, point.lat], circle });
    }
    positionOverlays();
  }

  function rebuildAreaMarks(): void {
    areaMarkLayer.replaceChildren();
    areaMarks = [];
    if (data === null) return;
    for (const area of data.areas) {
      if (area.kind !== "watched") continue;
      const group = svgEl("g", { class: "watch-area-mark" });
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "-1");
      group.setAttribute("aria-label", `${area.name}: open area`);
      group.append(
        svgTitle(area.name),
        svgEl("rect", { x: -7, y: -7, width: 14, height: 14, fill: "none", class: "watch-area-mark-box" }),
        svgEl("circle", { cx: 0, cy: 0, r: 12, class: "watch-alert-hit" }),
      );
      group.addEventListener("click", (clicked) => {
        clicked.stopPropagation();
        if (!destroyed) opts.onSelectArea?.(area.id);
      });
      areaMarkLayer.append(group);
      areaMarks.push({ area, group });
    }
  }

  function drawAreaSelection(areaId: string | null): void {
    if (!ready) return;
    const area = areaId === null ? undefined : data?.areas.find((entry) => entry.id === areaId);
    void source("watch-area-sel")?.setData(
      area === undefined ? { type: "FeatureCollection", features: [] } : areaFeatures([area]),
    );
  }

  function showRegion(region: {
    bounds: [number, number, number, number];
    areaId: string | null;
    alertId: string | null;
  }): void {
    if (destroyed) return;
    stopReachTimers();
    focus = null;
    appliedAlertId = null;
    clearFocusShapes();
    clearContactFocus();
    focusing = true;
    selectedAlertId = region.alertId;
    selectedAreaId = region.areaId;
    drawTrail(null);
    if (ready) void source("watch-reach")?.setData({ type: "FeatureCollection", features: [] });
    for (const dot of dots) {
      const selected = dot.alert.id === region.alertId;
      dot.group.classList.toggle("is-selected", selected);
      if (selected) alertLayer.append(dot.group);
    }
    if (!ready) {
      pendingRegion = { bounds: region.bounds, areaId: region.areaId };
      return;
    }
    drawAreaSelection(region.areaId);
    applyLayerVisibility();
    const [w, s, e, n] = region.bounds;
    fitBox(
      [
        [w, s],
        [e, n],
      ],
      9,
    );
    positionOverlays();
  }

  function setData(next: WatchResponse): void {
    if (destroyed) return;
    data = next;

    shipStore = new Map();
    glides.clear();
    for (const ship of next.ships) {
      if (!Number.isFinite(ship.lat) || !Number.isFinite(ship.lon)) continue;
      shipStore.set(ship.mmsi, {
        lon: ship.lon,
        lat: ship.lat,
        watch: ship.watchlist === "sanctioned" || ship.watchlist === "shadow_fleet" ? 1 : 0,
        sanctioned: ship.watchlist === "sanctioned" ? 1 : 0,
        sog: ship.sog ?? null,
        // `cog` comes from /api/watch but is not in the api.ts type yet.
        cog: (ship as { cog?: number | null }).cog ?? null,
      });
    }

    if (ready) {
      const areas = source("watch-areas");
      if (areas !== undefined) void areas.setData(areaFeatures(next.areas));
      pushShips();
    }

    rebuildAlertDots();
    rebuildAreaLabels();
    rebuildAreaMarks();
    if (selectedAlertId !== null) {
      for (const dot of dots) dot.group.classList.toggle("is-selected", dot.alert.id === selectedAlertId);
    }
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
    if (!moveCamera) return;
    flyToFocus(alert);
  }

  function applyFocus(
    alert: WatchAlert,
    options: { animate: boolean; moveCamera: boolean },
  ): void {
    if (destroyed) return;
    if (ready) rememberCamera();
    focusing = true;
    focus = alert;
    selectedAlertId = alert.id;
    selectedAreaId = null;
    drawAreaSelection(null);
    applyLayerVisibility();

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
    drawTrail(alert);
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
    selectedAlertId = null;
    selectedAreaId = null;
    pendingRegion = null;
    clearFocusShapes();
    clearContactFocus();
    setRadar(null);
    drawTrail(null);
    drawAreaSelection(null);
    applyLayerVisibility();
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
          duration: MOVE_MS,
          essential: true,
        });
      }
    }
    positionOverlays();
  }

  // ---- dossier track hook (#182) --------------------------------------------
  //
  // One hull's own path, drawn above the ship field in the AIS colour. The
  // dossier owns the data; the map only renders what it is handed.

  function dossierTrackFeatures(): GeoJSON.FeatureCollection {
    const features: GeoJSON.Feature[] = [];
    if (dossierTrack.length >= 2) {
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: dossierTrack.map(([lon, lat]) => [lon, lat]),
        },
        properties: {},
      });
    }
    const dots =
      dossierTrack.length === 1
        ? [dossierTrack[0]]
        : dossierTrack.length > 1
          ? [dossierTrack[0], dossierTrack[dossierTrack.length - 1]]
          : [];
    dots.forEach((point, index) => {
      if (point === undefined) return;
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [point[0], point[1]] },
        properties: { role: index === 0 ? "start" : "end" },
      });
    });
    return { type: "FeatureCollection", features };
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
      data: storeFeatures(shipStore),
    });
    map.addSource("watch-graticule", { type: "geojson", data: graticule() });
    map.addSource("watch-radar", {
      type: "geojson",
      data: radarView === null ? { type: "FeatureCollection", features: [] } : cellFeatures(radarView),
    });
    map.addSource("watch-trail", {
      type: "geojson",
      lineMetrics: true,
      data: { type: "FeatureCollection", features: [] },
    });
    map.addSource("watch-trail-after", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    map.addSource("watch-area-sel", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    map.addLayer({
      id: "watch-graticule",
      type: "line",
      source: "watch-graticule",
      paint: { "line-color": colors.lineStrong, "line-opacity": 0.3, "line-width": 0.5 },
    });
    // Radar-seen vessels with no matching broadcast: radar is the one meaning.
    map.addLayer({
      id: "watch-radar-fill",
      type: "fill",
      source: "watch-radar",
      paint: {
        "fill-color": colors.radar,
        "fill-opacity": ["*", 1, ["interpolate", ["linear"], ["get", "w"], 0, 0.14, 1, 0.9]],
        "fill-opacity-transition": { duration: 60 },
      },
    });
    // Dossier hook (#182): one hull's 12 h path, empty until a dossier opens.
    map.addSource("watch-dossier-track", {
      type: "geojson",
      data: dossierTrackFeatures(),
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
      id: "watch-area-selected",
      type: "line",
      source: "watch-area-sel",
      paint: { "line-color": colors.text1, "line-opacity": 0.9, "line-width": 1.25 },
    });
    // The ship's own path: thin, fading in from the oldest fix to the last one.
    map.addLayer({
      id: "watch-trail-line",
      type: "line",
      source: "watch-trail",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-width": 1.25,
        "line-gradient": [
          "interpolate",
          ["linear"],
          ["line-progress"],
          0,
          rgba(colors.text1, 0),
          1,
          rgba(colors.text1, 0.95),
        ],
      },
    });
    map.addLayer({
      id: "watch-trail-after-line",
      type: "line",
      source: "watch-trail-after",
      paint: {
        "line-color": colors.text2,
        "line-opacity": 0.5,
        "line-width": 1,
        "line-dasharray": [2, 3],
      },
    });
    map.addLayer({
      id: "watch-reach-fill",
      type: "fill",
      source: "watch-reach",
      paint: {
        "fill-color": colors.prism,
        "fill-opacity": 0.04,
      },
    });
    map.addLayer({
      id: "watch-reach-edge",
      type: "line",
      source: "watch-reach",
      paint: {
        "line-color": colors.prism,
        "line-opacity": 0.75,
        "line-width": 1,
      },
    });

    // Every ship: a muted neutral dot. Hulls under way are drawn once, as chevrons.
    map.addLayer({
      id: "watch-ship",
      type: "circle",
      source: "watch-ships",
      filter: ["all", ["==", ["get", "watch"], 0], ["!", MOVING_FILTER]],
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 0.8, 5, 1.5, 8, 2.6, 11, 3.6],
        "circle-color": colors.ais,
        "circle-opacity": 0.55,
      },
    });
    map.addLayer({
      id: "watch-ship-watch",
      type: "circle",
      source: "watch-ships",
      filter: ["all", ["==", ["get", "watch"], 1], ["!", MOVING_FILTER]],
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.2, 5, 2.2, 8, 3.4, 11, 4.4],
        "circle-color": ["match", ["get", "sanctioned"], 1, colors.alert, colors.ais],
        "circle-opacity": 0.8,
      },
    });

    // SDF chevron, point up: 7 x 10 white-on-transparent in a 16 x 16 canvas.
    if (!map.hasImage("watch-chev")) {
      const ctx = Object.assign(document.createElement("canvas"), { width: 16, height: 16 }).getContext("2d");
      if (ctx !== null) {
        ctx.fillStyle = "#FFFFFF";
        ctx.beginPath();
        ctx.moveTo(8, 3); ctx.lineTo(11.5, 13); ctx.lineTo(8, 10.2); ctx.lineTo(4.5, 13);
        ctx.fill();
        map.addImage("watch-chev", ctx.getImageData(0, 0, 16, 16), { sdf: true });
      }
    }
    map.addLayer({
      id: "watch-ship-chevron", type: "symbol", source: "watch-ships", filter: MOVING_FILTER,
      layout: { "icon-image": "watch-chev", "icon-rotate": ["get", "cog"], "icon-rotation-alignment": "map",
        "icon-allow-overlap": true, "icon-ignore-placement": true,
        "icon-size": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 10, 0.9] },
      paint: { "icon-color": ["match", ["get", "sanctioned"], 1, colors.alert, colors.ais], "icon-opacity": 0.8 },
    });
    // The ship dots are a pixel across; this transparent disc is the click target
    // and is hidden with the rest of the vessel layers. Dossier hook (#182).
    map.addLayer({
      id: "watch-ship-hit",
      type: "circle",
      source: "watch-ships",
      paint: {
        "circle-radius": 7,
        "circle-color": colors.text1,
        "circle-opacity": 0,
      },
    });

    // Dossier track: a hairline path with a hollow origin and a filled last fix.
    map.addLayer({
      id: "watch-dossier-track-line",
      type: "line",
      source: "watch-dossier-track",
      filter: ["==", ["geometry-type"], "LineString"],
      paint: {
        "line-color": colors.track,
        "line-opacity": 1,
        "line-width": 1.25,
      },
    });
    map.addLayer({
      id: "watch-dossier-track-dot",
      type: "circle",
      source: "watch-dossier-track",
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-radius": ["match", ["get", "role"], "end", 3, 2],
        "circle-color": ["match", ["get", "role"], "end", colors.text1, colors.text3],
        "circle-opacity": 1,
        "circle-stroke-color": colors.sea,
        "circle-stroke-width": 1,
      },
    });

    // Dossier hook (#182): any ship dot opens that hull's dossier.
    if (opts.onSelectVessel !== undefined) {
      const selectVessel = opts.onSelectVessel;
      map.on("click", "watch-ship-hit", (event) => {
        const mmsi = Number(event.features?.[0]?.properties?.["mmsi"]);
        if (Number.isFinite(mmsi)) selectVessel(mmsi);
      });
      map.on("mouseenter", "watch-ship-hit", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "watch-ship-hit", () => {
        map.getCanvas().style.cursor = "";
      });
    }

    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }), "bottom-left");

    ready = true;
    sizeSvg();
    rebuildAlertDots();
    rebuildAreaLabels();
    applyLayerVisibility();
    positionOverlays();

    applyRadarIntensity();
    pushShips();

    // A focus applied before the first frame (deep link) owns the camera now;
    // otherwise the camera would stay on the world fit.
    if (focus !== null) {
      const alert = focus;
      drawTrail(alert);
      flyToLast(alert, true);
      animateReach(alert, false);
    } else if (pendingRegion !== null) {
      const region = pendingRegion;
      pendingRegion = null;
      showRegion({ bounds: region.bounds, areaId: region.areaId, alertId: selectedAlertId });
    }
  });

  // A click inside a watched area (or a few pixels around a small one) opens it.
  // Alert dots and markers stop their own clicks, so this only sees the rest.
  function watchedAreaAt(point: { x: number; y: number }): WatchArea | null {
    let best: { area: WatchArea; size: number } | null = null;
    for (const area of areaOrder) {
      if (area.kind !== "watched") continue;
      const [w, s, e, n] = area.bbox;
      const a = map.project([w, n]);
      const b = map.project([e, s]);
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      const halfW = Math.max(Math.abs(b.x - a.x) / 2, 12);
      const halfH = Math.max(Math.abs(b.y - a.y) / 2, 12);
      if (Math.abs(point.x - cx) <= halfW && Math.abs(point.y - cy) <= halfH) {
        const size = halfW * halfH;
        if (best === null || size < best.size) best = { area, size };
      }
    }
    return best?.area ?? null;
  }

  map.on("click", (event) => {
    if (destroyed || !layerState.areas) return;
    const area = watchedAreaAt(event.point);
    if (area !== null) opts.onSelectArea?.(area.id);
  });
  map.on("mousemove", (event) => {
    if (destroyed) return;
    map.getCanvas().style.cursor = layerState.areas && watchedAreaAt(event.point) !== null ? "pointer" : "";
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

    showDossierTrack(track: readonly VesselTrackPoint[] | null): void {
      if (destroyed) return;
      dossierTrack = track === null ? [] : [...track];
      if (!ready) return;
      const src = source("watch-dossier-track");
      if (src !== undefined) void src.setData(dossierTrackFeatures());
    },

    showWorld,

    setReachVisible(visible: boolean): void {
      replayHidesReach = !visible;
      applyLayerVisibility();
    },

    replayMap: map, // #185
    fitArea(bbox: [number, number, number, number] | null): void {
      if (destroyed || !ready) return;
      const box: Bounds =
        bbox === null
          ? WORLD_BOUNDS
          : [
              [bbox[0], bbox[1]],
              [bbox[2], bbox[3]],
            ];
      map.fitBounds(box, {
        padding: bbox === null ? 12 : 48,
        maxZoom: 9,
        duration: prefersReducedMotion() ? 0 : MOVE_MS,
        essential: true,
      });
      savedCamera = null;
    },
    showRegion,
    setRadar,
    showSatelliteCheck,
    applyLive,
    setRadarIntensity(k: number): void {
      radarIntensity = Math.max(0, Math.min(1.2, k));
      applyRadarIntensity();
    },

    /** [search #183] fly to a hull from the top bar; no alert focus is required. */
    flyToVessel(lat: number, lon: number): void {
      if (destroyed || !ready) return;
      const center: LngLat = [lon, lat];
      if (prefersReducedMotion()) {
        map.jumpTo({ center, zoom: FOCUS_ZOOM });
        return;
      }
      map.flyTo({ center, zoom: FOCUS_ZOOM, duration: FOCUS_MS, essential: true });
    },

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
      if (glideTimer !== null) window.clearInterval(glideTimer);
      overlay.remove();
      controls.remove();
      layerPanel.remove();
      cursorEl.remove();
      map.remove();
    },
  };
}

/** The attribution line the world view shows under the map. */
export function watchAttribution(gfw = false): HTMLElement {
  return h(
    "div",
    { class: "watch-attrib" },
    gfw
      ? h(
          "span",
          null,
          h(
            "a",
            {
              href: "https://globalfishingwatch.org",
              target: "_blank",
              rel: "noopener noreferrer",
            },
            "Powered by Global Fishing Watch",
          ),
          " \u00b7 ",
        )
      : null,
    "Positions: our AIS recording \u00b7 Sanctions: ",
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
