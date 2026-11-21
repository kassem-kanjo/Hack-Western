import * as maplibregl from "maplibre-gl";

/** Zoom at which the fine (1:10m) coastline takes over from the 1:50m one. */
const FINE_LAND_MINZOOM = 6;

/**
 * Read a theme colour from a CSS custom property, falling back to the given
 * value so the style stays valid when the property is not defined.
 */
function themeColor(variable: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(variable)
    .trim();
  return value || fallback;
}

/**
 * A fully offline basemap: sea background plus land/coast fill from two
 * committed Natural Earth GeoJSON files — the small 1:50m one for the whole
 * world, and a simplified 1:10m one that takes over from zoom 6 so the coast
 * keeps its shape instead of turning into a chain of straight segments. No
 * glyphs, sprite or remote URLs, so the console renders with the network
 * switched off.
 */
export function offlineStyle(): maplibregl.StyleSpecification {
  const landFill = themeColor("--map-land", "#1a2333");
  const coast = themeColor("--map-coast", "#2c3a52");

  return {
    version: 8,
    sources: {
      land: {
        type: "geojson",
        // Relative to the document, so it works in dev, preview and under any
        // sub-path the static build is served from.
        data: "basemap/land.geojson",
      },
      landFine: {
        type: "geojson",
        data: "basemap/land-10m.geojson",
      },
    },
    layers: [
      {
        id: "sea",
        type: "background",
        paint: {
          "background-color": themeColor("--map-sea", "#0b1220"),
        },
      },
      // 1:50m, the whole-world view. The two resolutions never draw together:
      // a layer is visible from minzoom up to (excluding) maxzoom.
      {
        id: "land",
        type: "fill",
        source: "land",
        maxzoom: FINE_LAND_MINZOOM,
        paint: {
          "fill-color": landFill,
        },
      },
      {
        id: "coast",
        type: "line",
        source: "land",
        maxzoom: FINE_LAND_MINZOOM,
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": coast,
          "line-width": 0.6,
        },
      },
      // 1:10m from zoom 6 in, where the 50m vertices would be visible as
      // corners. Same paint, so crossing zoom 6 is a change in detail only.
      {
        id: "land-fine",
        type: "fill",
        source: "landFine",
        minzoom: FINE_LAND_MINZOOM,
        paint: {
          "fill-color": landFill,
        },
      },
      {
        id: "coast-fine",
        type: "line",
        source: "landFine",
        minzoom: FINE_LAND_MINZOOM,
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": coast,
          "line-width": 0.6,
        },
      },
    ],
  };
}
