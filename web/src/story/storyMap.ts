/**
 * Map helpers for the story screen (#274).
 *
 * Pure maths first (geodesic circle, progressive trail, dot payloads), then the
 * one camera fit and the four MapLibre layers the beats draw into. The offline
 * style has no glyphs, so nothing here uses text-field: the one map label is an
 * HTML element the screen positions with map.project().
 *
 * The reach circle is a true geodesic ring (destination point on a sphere), not
 * a degree-space oval: at 45 degrees north a circle drawn in lon/lat degrees is
 * noticeably squashed, and the whole beat is about distance.
 */

import type * as maplibregl from "maplibre-gl";

import { distanceKm, KM_PER_KN_HOUR } from "../map/watchMap";

export type LngLat = [number, number];
export type Bounds = [[number, number], [number, number]];

const EARTH_RADIUS_KM = 6371.0088;
const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;

/** The story's camera fit: the case sits in the right of the screen, clear of the text card. */
export const STORY_FIT_PADDING: maplibregl.PaddingOptions = {
  left: 540,
  right: 60,
  top: 90,
  bottom: 90,
};

/** One camera move, so a beat never bounces. */
export const STORY_MOVE_MS = 900;

/* ---- tokens --------------------------------------------------------------- */

/** Read a CSS custom property; the fallback keeps the style valid without a document. */
export function tokenColor(variable: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(variable).trim();
  return value || fallback;
}

export interface StoryPalette {
  track: string;
  reach: string;
  sea: string;
  vessel: string;
  vesselDim: string;
  dark: string;
  sanctioned: string;
}

/**
 * Snapshot the map palette once. The fill reuses --geo-reach with the token's
 * 4 % opacity, the same way watchMap draws its reach: MapLibre cannot consume
 * the rgba() fill token itself.
 */
export function readStoryPalette(): StoryPalette {
  return {
    track: tokenColor("--geo-track", "#A9B6C3"),
    reach: tokenColor("--geo-reach", "#C9D6E3"),
    sea: tokenColor("--surface-0", "#0C1218"),
    vessel: tokenColor("--geo-vessel", "#8797A9"),
    vesselDim: tokenColor("--geo-vessel-dim", "rgba(135, 151, 169, 0.45)"),
    dark: tokenColor("--st-dark", "#F0A33A"),
    sanctioned: tokenColor("--st-sanctioned", "#F26457"),
  };
}

/* ---- motion --------------------------------------------------------------- */

/** The one easing on the screen. Duration lives in beats.ts. */
export function easeInOut(t: number): number {
  const p = Math.min(1, Math.max(0, t));
  return p < 0.5 ? 2 * p * p : 1 - ((-2 * p + 2) ** 2) / 2;
}

/* ---- reach maths ---------------------------------------------------------- */

/**
 * Distance the speed cap allows by `atMs`: speed (kn) x 1.852 km per knot-hour
 * x hours. The same formula as reachRadiusKm() in map/watchMap.ts, kept local
 * so the story's alert shape does not have to pretend to be a WatchAlert.
 */
export function radiusKmAtSpeed(speedCapKn: number, lastMs: number, atMs: number): number {
  if (!Number.isFinite(speedCapKn) || !Number.isFinite(lastMs) || !Number.isFinite(atMs)) return 0;
  const hours = Math.max(0, atMs - lastMs) / 3_600_000;
  return Math.max(0, speedCapKn) * KM_PER_KN_HOUR * hours;
}

/** Radius in km at `minutes` after the last report. */
export function radiusKmAtMinutes(speedCapKn: number, minutes: number): number {
  if (!Number.isFinite(speedCapKn) || !Number.isFinite(minutes)) return 0;
  return Math.max(0, speedCapKn) * KM_PER_KN_HOUR * (Math.max(0, minutes) / 60);
}

/* ---- geodesic circle ------------------------------------------------------ */

function normLon(lon: number): number {
  let x = lon;
  while (x > 180) x -= 360;
  while (x < -180) x += 360;
  return x;
}

/** Forward geodesic: the point `distanceKm` from `centre` on `bearing` (radians). */
function destination(centre: LngLat, bearing: number, distanceKmValue: number): LngLat {
  const [lon, lat] = centre;
  const delta = distanceKmValue / EARTH_RADIUS_KM;
  const lat1 = lat * RAD;
  const lon1 = lon * RAD;
  const sinLat2 =
    Math.sin(lat1) * Math.cos(delta) +
    Math.cos(lat1) * Math.sin(delta) * Math.cos(bearing);
  const lat2 = Math.asin(Math.min(1, Math.max(-1, sinLat2)));
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearing) * Math.sin(delta) * Math.cos(lat1),
      Math.cos(delta) - Math.sin(lat1) * Math.sin(lat2),
    );
  return [normLon(lon2 * DEG), lat2 * DEG];
}

/**
 * A closed ring of `vertices` points (96 for the reach) around `centre`.
 * Returns [] for a zero radius: there is nothing to fill yet.
 */
export function circlePolygon(centre: LngLat, radiusKm: number, vertices = 96): LngLat[] {
  if (!(radiusKm > 0.01) || !Number.isFinite(radiusKm)) return [];
  const n = Math.max(8, Math.round(vertices));
  const ring: LngLat[] = [];
  for (let i = 0; i < n; i += 1) {
    ring.push(destination(centre, (i / n) * Math.PI * 2, radiusKm));
  }
  ring.push([ring[0][0], ring[0][1]]);
  return ring;
}

/* ---- the trail ------------------------------------------------------------ */

export interface TrailPath {
  coords: LngLat[];
  /** Cumulative geodesic km at each vertex; cumulative[0] is 0. */
  cumulative: number[];
  total: number;
}

/**
 * The recorded track up to the last report, ending exactly on alert.last.
 * Points after the last fix (a "next fix" in the same trail) are dropped: the
 * beat is about the track that was recorded before the transponder went off.
 */
export function trailCoords(
  trail: readonly StoryTrailPointInput[] | null | undefined,
  last: { lat: number; lon: number; t: string },
): LngLat[] {
  const lastMs = Date.parse(last.t);
  const coords: LngLat[] = [];
  for (const point of trail ?? []) {
    const lon = point[0];
    const lat = point[1];
    const rawTime = point[2];
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    const pointMs = typeof rawTime === "string" ? Date.parse(rawTime) : NaN;
    if (Number.isFinite(lastMs) && Number.isFinite(pointMs) && pointMs > lastMs + 1000) continue;
    const previous = coords[coords.length - 1];
    if (previous !== undefined && previous[0] === lon && previous[1] === lat) continue;
    coords.push([lon, lat]);
  }
  const end = coords[coords.length - 1];
  if (end === undefined || end[0] !== last.lon || end[1] !== last.lat) {
    coords.push([last.lon, last.lat]);
  }
  return coords;
}

/** A track point as the case JSON carries it (kept local so this module has no data types). */
type StoryTrailPointInput = [number, number, string];

export function trailPath(coords: readonly LngLat[]): TrailPath {
  const list = coords.map(([lon, lat]) => [lon, lat] as LngLat);
  const cumulative: number[] = [0];
  let total = 0;
  for (let i = 1; i < list.length; i += 1) {
    total += distanceKm(list[i - 1][1], list[i - 1][0], list[i][1], list[i][0]);
    cumulative.push(total);
  }
  return { coords: list, cumulative, total };
}

/**
 * The part of the track drawn after `fraction` of its length, with the last
 * segment split at the exact distance. Fewer than two points draws nothing.
 */
export function sliceTrail(path: TrailPath, fraction: number): LngLat[] {
  const { coords, cumulative, total } = path;
  if (coords.length === 0) return [];
  const p = Math.min(1, Math.max(0, fraction));
  if (p <= 0 || total <= 0) return [coords[0]];
  if (p >= 1) return coords.slice();

  const target = total * p;
  const out: LngLat[] = [coords[0]];
  for (let i = 1; i < coords.length; i += 1) {
    if (cumulative[i] <= target) {
      out.push(coords[i]);
      continue;
    }
    const segmentStart = cumulative[i - 1];
    const segmentLength = cumulative[i] - segmentStart;
    const k = segmentLength <= 0 ? 0 : (target - segmentStart) / segmentLength;
    if (k > 0.0001) {
      const from = coords[i - 1];
      const to = coords[i];
      out.push([from[0] + (to[0] - from[0]) * k, from[1] + (to[1] - from[1]) * k]);
    }
    break;
  }
  return out;
}

/* ---- bounds and camera ---------------------------------------------------- */

/** Lon/lat box around a set of points; a single point becomes a small box. */
export function pointsBounds(coords: readonly LngLat[]): Bounds | null {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  let seen = false;
  for (const [lon, lat] of coords) {
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    seen = true;
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  }
  if (!seen) return null;
  if (east - west < 1e-4 && north - south < 1e-4) {
    const pad = 0.08;
    return [
      [west - pad, south - pad],
      [east + pad, north + pad],
    ];
  }
  return [
    [west, south],
    [east, north],
  ];
}

/**
 * Fit the case (or the satellite detections) into the right of the screen.
 * `animate` false is the first framing: the screen opens already composed.
 */
export function fitStoryBounds(
  map: maplibregl.Map,
  coords: readonly LngLat[],
  opts: { animate?: boolean; maxZoom?: number } = {},
): Bounds | null {
  const bounds = pointsBounds(coords);
  if (bounds === null) return null;
  map.stop();
  map.fitBounds(bounds, {
    padding: STORY_FIT_PADDING,
    maxZoom: opts.maxZoom ?? 11,
    animate: opts.animate === true,
    duration: opts.animate === true ? STORY_MOVE_MS : 0,
    essential: true,
  });
  return bounds;
}

/* ---- layers --------------------------------------------------------------- */

export interface StoryDot {
  lon: number;
  lat: number;
  /** Already resolved to a CSS colour (tokens are read once at mount). */
  color: string;
  /** Screen pixels. */
  radius: number;
  /** 0..1. */
  opacity: number;
}

interface DotProps {
  color: string;
  radius: number;
  opacity: number;
}

export interface StoryMapLayers {
  palette: StoryPalette;
  /** Empty every source: the next beat starts on a clean map. */
  clear(): void;
  setTrail(coords: readonly LngLat[]): void;
  setRing(ring: readonly LngLat[] | null): void;
  setDots(dots: readonly StoryDot[]): void;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

const SOURCE_TRAIL = "story-trail";
const SOURCE_REACH = "story-reach";
const SOURCE_DOTS = "story-dots";

function lineCollection(coords: readonly LngLat[]): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  if (coords.length < 2) return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "LineString", coordinates: coords.map(([lon, lat]) => [lon, lat]) },
        properties: {},
      },
    ],
  };
}

function ringCollection(ring: readonly LngLat[] | null): GeoJSON.FeatureCollection<GeoJSON.Polygon> {
  if (ring === null || ring.length < 3) return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [ring.map(([lon, lat]) => [lon, lat])] },
        properties: {},
      },
    ],
  };
}

function dotCollection(dots: readonly StoryDot[]): GeoJSON.FeatureCollection<GeoJSON.Point, DotProps> {
  const features: GeoJSON.Feature<GeoJSON.Point, DotProps>[] = [];
  for (const dot of dots) {
    if (!Number.isFinite(dot.lon) || !Number.isFinite(dot.lat)) continue;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [dot.lon, dot.lat] },
      properties: { color: dot.color, radius: dot.radius, opacity: dot.opacity },
    });
  }
  return { type: "FeatureCollection", features };
}

/**
 * Add the story's four layers once the style is ready. Order, low to high:
 * reach fill, trail, ships/detections, reach edge. Only the edge is shared by
 * the circle's boundary and the fill.
 */
export function installStoryLayers(map: maplibregl.Map): StoryMapLayers {
  const palette = readStoryPalette();

  map.addSource(SOURCE_TRAIL, { type: "geojson", data: EMPTY });
  map.addSource(SOURCE_REACH, { type: "geojson", data: EMPTY });
  map.addSource(SOURCE_DOTS, { type: "geojson", data: EMPTY });

  map.addLayer({
    id: "story-reach-fill",
    type: "fill",
    source: SOURCE_REACH,
    paint: {
      "fill-color": palette.reach,
      "fill-opacity": 0.04,
    },
  });
  map.addLayer({
    id: "story-trail-line",
    type: "line",
    source: SOURCE_TRAIL,
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": palette.track,
      "line-width": 1.25,
      "line-opacity": 0.95,
    },
  });
  map.addLayer({
    id: "story-dots",
    type: "circle",
    source: SOURCE_DOTS,
    paint: {
      "circle-color": ["get", "color"],
      "circle-radius": ["get", "radius"],
      "circle-opacity": ["get", "opacity"],
      "circle-stroke-color": palette.sea,
      "circle-stroke-width": 1,
      "circle-stroke-opacity": 0.9,
    },
  });
  map.addLayer({
    id: "story-reach-edge",
    type: "line",
    source: SOURCE_REACH,
    paint: {
      "line-color": palette.reach,
      "line-width": 1,
      "line-opacity": 0.8,
    },
  });

  const dataSource = (id: string): maplibregl.GeoJSONSource | undefined =>
    map.getSource(id) as maplibregl.GeoJSONSource | undefined;

  return {
    palette,
    clear(): void {
      void dataSource(SOURCE_TRAIL)?.setData(EMPTY);
      void dataSource(SOURCE_REACH)?.setData(EMPTY);
      void dataSource(SOURCE_DOTS)?.setData(EMPTY);
    },
    setTrail(coords: readonly LngLat[]): void {
      void dataSource(SOURCE_TRAIL)?.setData(lineCollection(coords));
    },
    setRing(ring: readonly LngLat[] | null): void {
      void dataSource(SOURCE_REACH)?.setData(ringCollection(ring));
    },
    setDots(dots: readonly StoryDot[]): void {
      void dataSource(SOURCE_DOTS)?.setData(dotCollection(dots));
    },
  };
}
