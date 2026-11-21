/**
 * Two optional map layers for the Hormuz block, added to the overview map only
 * when the reader asks for them:
 *
 * - "Radar, not broadcasting": every grid cell with at least one radar
 *   detection that matched no AIS broadcast. Violet fill (the could-have-been
 *   family) with a blue radar rim, radius by cell count.
 * - "Loitering hotspots": GFW loitering clusters as hollow white rings, radius
 *   by event count. White because a hotspot is neither a dark gap nor a radar
 *   detection; it is a place to look.
 *
 * Nothing is added until the style is ready, so mounting this before MapLibre's
 * `load` is safe. Destroy is tolerant: the overview map may already be gone.
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
const RADAR_LAYER = "hormuz-radar-circles";
const HOTSPOT_SOURCE = "hormuz-hotspots";
const HOTSPOT_LAYER = "hormuz-hotspot-rings";

interface SizedProperties {
  n: number;
  r: number;
}

/** 2 px for the thinnest cell, 16 px for the busiest; area tracks the count. */
function radius(n: number, max: number, floor: number, span: number): number {
  const scale = max > 0 ? Math.sqrt(Math.max(0, n) / max) : 0;
  return floor + span * scale;
}

function sized(
  points: { lat: number; lon: number; n: number }[],
  floor: number,
  span: number,
): GeoJSON.FeatureCollection<GeoJSON.Point, SizedProperties> {
  const max = points.reduce((best, point) => Math.max(best, point.n), 0);
  return {
    type: "FeatureCollection",
    features: points.map((point) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [point.lon, point.lat] },
      properties: { n: point.n, r: radius(point.n, max, floor, span) },
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

  const radarData = sized(grid, 2, 14);
  const hotspotData = sized(hotspots, 3, 11);
  const violet = themeColor("--c-prism-stroke", "#A78BFA");
  const blue = themeColor("--c-sar", "#5BB8FF");
  const white = themeColor("--c-text-1", "#EAF0F6");
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

  const visibility = (): void => {
    if (!applied || destroyed) return;
    map.setLayoutProperty(RADAR_LAYER, "visibility", radarOn ? "visible" : "none");
    map.setLayoutProperty(HOTSPOT_LAYER, "visibility", hotspotsOn ? "visible" : "none");
  };

  const install = (): void => {
    if (applied || destroyed) return;

    map.addSource(RADAR_SOURCE, { type: "geojson", data: radarData });
    map.addLayer({
      id: RADAR_LAYER,
      type: "circle",
      source: RADAR_SOURCE,
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["get", "r"],
        "circle-color": violet,
        "circle-opacity": 0.32,
        "circle-stroke-color": blue,
        "circle-stroke-width": 1,
        "circle-stroke-opacity": 0.9,
      },
    });

    map.addSource(HOTSPOT_SOURCE, { type: "geojson", data: hotspotData });
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
      if (on && zoomOnEnable) zoomTo(grid);
    },
    setHotspots(on: boolean): void {
      hotspotsOn = on;
      visibility();
      if (on && zoomOnEnable) zoomTo(hotspots);
    },
    destroy(): void {
      destroyed = true;
      try {
        if (map.getLayer(RADAR_LAYER)) map.removeLayer(RADAR_LAYER);
        if (map.getSource(RADAR_SOURCE)) map.removeSource(RADAR_SOURCE);
        if (map.getLayer(HOTSPOT_LAYER)) map.removeLayer(HOTSPOT_LAYER);
        if (map.getSource(HOTSPOT_SOURCE)) map.removeSource(HOTSPOT_SOURCE);
      } catch {
        // The map has already been torn down; there is nothing left to remove.
      }
    },
  };
}
