import * as maplibregl from "maplibre-gl";

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
 * A fully offline basemap: sea background plus land/coast fill from the
 * committed Natural Earth land-50m GeoJSON. No glyphs, sprite or remote URLs,
 * so the console renders with the network switched off.
 */
export function offlineStyle(): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: {
      land: {
        type: "geojson",
        // Relative to the document, so it works in dev, preview and under any
        // sub-path the static build is served from.
        data: "basemap/land.geojson",
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
      {
        id: "land",
        type: "fill",
        source: "land",
        paint: {
          "fill-color": themeColor("--map-land", "#1a2333"),
        },
      },
      {
        id: "coast",
        type: "line",
        source: "land",
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": themeColor("--map-coast", "#2c3a52"),
          "line-width": 0.6,
        },
      },
    ],
  };
}
