/**
 * Two optional map layers for the Hormuz block, added to a map only when the
 * reader asks for them.
 *
 * - "Radar, not broadcasting": a heatmap whose weight is the cell's detection
 *   count normalised by the busiest visible cell, so the strait reads as one
 *   field of intensity instead of a 0.1 degree grid of identical circles. The
 *   ramp runs from transparent through radar blue to a brighter violet, and
 *   cells below three detections are left out: those are the ordinary trickle,
 *   not the finding. From zoom 8 the heat fades back and small circles mark the
 *   exact cells, for anyone who wants the precise grid.
 * - "Loitering hotspots": the five busiest GFW clusters, as hollow white rings
 *   (radius by event count) numbered 1-5 on hover. White because a hotspot is
 *   neither a dark gap nor a radar detection; it is a place to look.
 *
 * The basemap is offline with no glyphs, so the hover numeral is a locally
 * canvas-drawn icon (same approach as the radar diamonds) whose opacity is
 * driven by MapLibre feature state. Nothing is added until the style is ready,
 * so mounting this before MapLibre's `load` is safe. Destroy is tolerant: the
 * map may already be gone.
 */

import type * as maplibregl from "maplibre-gl";

import { rgba, themeColor } from "../map/theme";
import type { HormuzGridCell, HormuzHotspot } from "./hormuzTypes";

export interface HormuzLayersHandle {
  setRadar(on: boolean): void;
  setHotspots(on: boolean): void;
  destroy(): void;
}

export interface HormuzLayerOptions {
  /**
   * Fit the map to a layer's own extent when it is switched on (default true).
   * The focus view starts already framed on the strait, so it turns this off.
   */
  zoomOnEnable?: boolean;
}

const RADAR_SOURCE = "hormuz-radar";
const RADAR_HEAT_LAYER = "hormuz-radar-heat";
const RADAR_CELL_LAYER = "hormuz-radar-cells";
const HOTSPOT_SOURCE = "hormuz-hotspots";
const HOTSPOT_LAYER = "hormuz-hotspot-rings";
const HOTSPOT_LABEL_LAYER = "hormuz-hotspot-labels";
const HEAT_MINZOOM = 8;
const HOTSPOT_RANKS = 5;

/** Cells this thin are the background trickle, not a radar finding. */
const MIN_CELL = 3;

/** Rank icons are authored at 16 CSS px and rasterised at 2x for sharp edges. */
const BADGE_SIZE = 16;
const BADGE_SCALE = 2;

interface RadarProperties {
  n: number;
  /** n / busiest visible cell: the heatmap weight, always in (0, 1]. */
  w: number;
  /** Exact-cell marker radius in px. */
  r: number;
}

interface HotspotProperties {
  n: number;
  /** Ring radius in px. */
  r: number;
  rank: number;
  /** Pre-built icon id, so the style needs no string concatenation. */
  icon: string;
}

function maxCount(points: { n: number }[]): number {
  return points.reduce((best, point) => Math.max(best, point.n), 0);
}

/** 1.5 px for the thinnest visible cell, 5.5 px for the busiest; area tracks count. */
function cellRadius(n: number, max: number): number {
  const scale = max > 0 ? Math.sqrt(Math.max(0, n) / max) : 0;
  return 1.5 + 4 * scale;
}

/** Rings start at 8 px and grow to 19 px, so all five stay readable when framed. */
function ringRadius(n: number, max: number): number {
  const scale = max > 0 ? Math.sqrt(Math.max(0, n) / max) : 0;
  return 8 + 11 * scale;
}

function radarData(grid: HormuzGridCell[]): GeoJSON.FeatureCollection<
  GeoJSON.Point,
  RadarProperties
> {
  const visible = grid.filter((cell) => cell.n >= MIN_CELL);
  const max = maxCount(visible);
  return {
    type: "FeatureCollection",
    features: visible.map((cell) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [cell.lon, cell.lat] },
      properties: {
        n: cell.n,
        w: max > 0 ? cell.n / max : 0,
        r: cellRadius(cell.n, max),
      },
    })),
  };
}

function hotspotData(hotspots: HormuzHotspot[]): GeoJSON.FeatureCollection<
  GeoJSON.Point,
  HotspotProperties
> {
  const ranked = [...hotspots].sort((a, b) => b.n - a.n).slice(0, HOTSPOT_RANKS);
  const max = maxCount(ranked);
  return {
    type: "FeatureCollection",
    features: ranked.map((hotspot, index) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [hotspot.lon, hotspot.lat] },
      properties: {
        n: hotspot.n,
        r: ringRadius(hotspot.n, max),
        rank: index + 1,
        icon: `hormuz-rank-${index + 1}`,
      },
    })),
  };
}

function bounds(points: { lat: number; lon: number }[]): [[number, number], [number, number]] | null {
  if (points.length === 0) return null;
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const point of points) {
    west = Math.min(west, point.lon);
    east = Math.max(east, point.lon);
    south = Math.min(south, point.lat);
    north = Math.max(north, point.lat);
  }
  return [
    [west, south],
    [east, north],
  ];
}

function reducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Blend a hex token toward white; non-hex input is returned untouched. */
function brighter(hex: string, amount: number): string {
  const raw = hex.trim().replace(/^#/, "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return hex;
  const n = Number.parseInt(full, 16);
  const mix = Math.min(1, Math.max(0, amount));
  const channel = (value: number): string =>
    Math.round(value + (255 - value) * mix)
      .toString(16)
      .padStart(2, "0");
  return `#${channel((n >> 16) & 0xff)}${channel((n >> 8) & 0xff)}${channel(n & 0xff)}`;
}

/** A rank badge: dark disc, white rim, white numeral. */
function rankImage(rank: number, sea: string, ink: string, font: string): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = BADGE_SIZE * BADGE_SCALE;
  canvas.height = BADGE_SIZE * BADGE_SCALE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas context unavailable");
  ctx.scale(BADGE_SCALE, BADGE_SCALE);

  const centre = BADGE_SIZE / 2;
  ctx.beginPath();
  ctx.arc(centre, centre, centre - 1, 0, Math.PI * 2);
  ctx.fillStyle = rgba(sea, 0.92);
  ctx.fill();
  ctx.globalAlpha = 0.9;
  ctx.lineWidth = 1;
  ctx.strokeStyle = ink;
  ctx.stroke();
  ctx.globalAlpha = 1;

  ctx.fillStyle = ink;
  ctx.font = `600 10px ${font}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(rank), centre, centre + 0.5);
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

export function hormuzLayers(
  map: maplibregl.Map,
  grid: HormuzGridCell[],
  hotspots: HormuzHotspot[],
  options: HormuzLayerOptions = {},
): HormuzLayersHandle {
  const zoomOnEnable = options.zoomOnEnable !== false;
  let applied = false;
  let radarOn = false;
  let hotspotsOn = false;
  let destroyed = false;
  let hovered: string | number | null = null;

  const radar = radarData(grid);
  const ranked = [...hotspots].sort((a, b) => b.n - a.n).slice(0, HOTSPOT_RANKS);
  const hotspotSource = hotspotData(ranked);
  const radarFrame = radar.features.map((feature) => ({
    lat: feature.geometry.coordinates[1],
    lon: feature.geometry.coordinates[0],
  }));

  const violet = themeColor("--c-prism-stroke", "#A78BFA");
  const brightViolet = brighter(violet, 0.42);
  const blue = themeColor("--c-sar", "#5BB8FF");
  const white = themeColor("--c-text-1", "#EAF0F6");
  const sea = themeColor("--map-sea", "#0b1220");
  const mono = themeColor("--font-mono", "ui-monospace, SFMono-Regular, Menlo, monospace");
  const noFill = rgba("#000000", 0);

  const zoomTo = (points: { lat: number; lon: number }[]): void => {
    const box = bounds(points);
    if (box === null || destroyed) return;
    map.fitBounds(box, {
      padding: 56,
      maxZoom: 7,
      duration: reducedMotion() ? 0 : 600,
    });
  };

  const clearHover = (): void => {
    if (hovered === null || destroyed) return;
    try {
      map.setFeatureState({ source: HOTSPOT_SOURCE, id: hovered }, { hover: false });
    } catch {
      // The source may already be gone during teardown.
    }
    hovered = null;
  };

  const visibility = (): void => {
    if (!applied || destroyed) return;
    for (const layerId of [RADAR_HEAT_LAYER, RADAR_CELL_LAYER]) {
      map.setLayoutProperty(layerId, "visibility", radarOn ? "visible" : "none");
    }
    for (const layerId of [HOTSPOT_LAYER, HOTSPOT_LABEL_LAYER]) {
      map.setLayoutProperty(layerId, "visibility", hotspotsOn ? "visible" : "none");
    }
    if (!hotspotsOn) clearHover();
  };

  const onHotspotMove = (event: maplibregl.MapLayerMouseEvent): void => {
    if (!hotspotsOn || destroyed) return;
    const feature = event.features?.[0];
    if (!feature || feature.id === undefined || feature.id === null) {
      clearHover();
      return;
    }
    if (hovered === feature.id) return;
    clearHover();
    hovered = feature.id;
    try {
      map.setFeatureState({ source: HOTSPOT_SOURCE, id: hovered }, { hover: true });
    } catch {
      hovered = null;
    }
  };

  const onHotspotLeave = (): void => clearHover();

  const install = (): void => {
    if (applied || destroyed) return;

    map.addSource(RADAR_SOURCE, { type: "geojson", data: radar });
    map.addLayer({
      id: RADAR_HEAT_LAYER,
      type: "heatmap",
      source: RADAR_SOURCE,
      layout: { visibility: "none" },
      paint: {
        // Weight is the count over the busiest visible cell; radius is screen
        // space and grows with zoom so the field stays continuous when framed
        // on the strait and tightens as the exact-cell layer takes over.
        "heatmap-weight": ["get", "w"],
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 4, 8, 6, 20, 8, 32, 12, 52],
        "heatmap-intensity": 1.6,
        "heatmap-color": [
          "interpolate",
          ["linear"],
          ["heatmap-density"],
          0,
          noFill,
          0.08,
          rgba(blue, 0.12),
          0.25,
          rgba(blue, 0.45),
          0.5,
          rgba(violet, 0.65),
          0.72,
          rgba(violet, 0.85),
          1,
          rgba(brightViolet, 0.95),
        ],
        // Hand over to the exact cells: the field is gone by zoom 8, so a
        // zoomed-in view is a grid of measurements, not a grid of glows.
        "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0.92, HEAT_MINZOOM, 0],
      },
    });
    map.addLayer({
      id: RADAR_CELL_LAYER,
      type: "circle",
      source: RADAR_SOURCE,
      minzoom: HEAT_MINZOOM,
      filter: [">=", ["get", "n"], MIN_CELL],
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["get", "r"],
        "circle-color": rgba(violet, 0.55),
        "circle-stroke-color": blue,
        "circle-stroke-width": 1,
        "circle-stroke-opacity": 0.85,
      },
    });

    for (let rank = 1; rank <= HOTSPOT_RANKS; rank += 1) {
      const id = `hormuz-rank-${rank}`;
      if (map.hasImage(id)) continue;
      map.addImage(id, rankImage(rank, sea, white, mono), { pixelRatio: BADGE_SCALE });
    }

    map.addSource(HOTSPOT_SOURCE, {
      type: "geojson",
      data: hotspotSource,
      generateId: true,
    });
    map.addLayer({
      id: HOTSPOT_LAYER,
      type: "circle",
      source: HOTSPOT_SOURCE,
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["get", "r"],
        "circle-color": noFill,
        "circle-stroke-color": white,
        "circle-stroke-width": 1.5,
        "circle-stroke-opacity": 0.85,
      },
    });
    map.addLayer({
      id: HOTSPOT_LABEL_LAYER,
      type: "symbol",
      source: HOTSPOT_SOURCE,
      layout: {
        "icon-image": ["get", "icon"],
        "icon-size": 1,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        visibility: "none",
      },
      paint: {
        // The numeral appears only for the ring under the pointer.
        "icon-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0],
      },
    });

    map.on("mousemove", HOTSPOT_LAYER, onHotspotMove);
    map.on("mouseleave", HOTSPOT_LAYER, onHotspotLeave);

    applied = true;
    visibility();
  };

  if (map.isStyleLoaded()) {
    install();
  } else {
    map.once("load", install);
  }

  return {
    setRadar(on: boolean): void {
      radarOn = on;
      visibility();
      if (on && zoomOnEnable) zoomTo(radarFrame);
    },
    setHotspots(on: boolean): void {
      hotspotsOn = on;
      visibility();
      if (on && zoomOnEnable) zoomTo(ranked);
    },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      try {
        map.off("mousemove", HOTSPOT_LAYER, onHotspotMove);
        map.off("mouseleave", HOTSPOT_LAYER, onHotspotLeave);
        for (const layerId of [HOTSPOT_LABEL_LAYER, HOTSPOT_LAYER, RADAR_CELL_LAYER, RADAR_HEAT_LAYER]) {
          if (map.getLayer(layerId)) map.removeLayer(layerId);
        }
        if (map.getSource(HOTSPOT_SOURCE)) map.removeSource(HOTSPOT_SOURCE);
        if (map.getSource(RADAR_SOURCE)) map.removeSource(RADAR_SOURCE);
        for (let rank = 1; rank <= HOTSPOT_RANKS; rank += 1) {
          const id = `hormuz-rank-${rank}`;
          if (map.hasImage(id)) map.removeImage(id);
        }
      } catch {
        // The map has already been torn down; there is nothing left to remove.
      }
    },
  };
}
