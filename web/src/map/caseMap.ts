/**
 * Case-file map: the space-time prism (lens) at one frame, the gap track and
 * every radar detection for the case.
 *
 * The style is fully offline (local land GeoJSON, canvas-generated icons, no
 * glyph server) and has no text layers; labels are HTML markers instead.
 */

import * as maplibregl from "maplibre-gl";

import type { CaseDetail, GeoPolygon } from "../api";
import { offlineStyle } from "../basemap";
import { fmtUtc } from "../format";
import { isCandidateLive } from "../model/frames";
import { addDiamondImages } from "./diamonds";
import { rgba, themeColor } from "./theme";

/** Layer groups the case view can switch on and off. */
export type CaseLayerGroup = "gap" | "lens" | "likely" | "radar" | "footprint";

const GROUP_LAYERS: Record<CaseLayerGroup, string[]> = {
  gap: ["track-line", "ends-circle"],
  lens: ["lens-fill", "lens-line"],
  likely: ["likely-fill"],
  radar: ["radar-symbols"],
  footprint: ["footprint-line"],
};

export interface CaseMapHandle {
  setFrame(index: number): void;
  setLayerVisible(group: CaseLayerGroup, visible: boolean): void;
  destroy(): void;
}

interface EndProperties {
  role: "off" | "on";
}

interface RadarProperties {
  index: number;
  live: boolean;
  inside: boolean | null;
  verdict: string;
}

function polygonData(
  polygon: GeoPolygon | null,
): GeoJSON.FeatureCollection<GeoJSON.Polygon> {
  return {
    type: "FeatureCollection",
    features: polygon
      ? [
          {
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: polygon.coordinates,
            },
            properties: {},
          },
        ]
      : [],
  };
}

export function mountCaseMap(
  container: HTMLElement,
  detail: CaseDetail,
): CaseMapHandle {
  const map = new maplibregl.Map({
    container,
    style: offlineStyle(),
    center: detail.off.pos,
    zoom: 5,
    attributionControl: false,
    interactive: true,
  });

  const amber = themeColor("--c-gap", "#FFB020");
  const prism = themeColor("--c-prism-stroke", "#A78BFA");
  const prismFill = rgba(prism, 0.16);
  const likelyFill = themeColor("--c-prism-likely", "rgba(167, 139, 250, 0.35)");
  const noFill = rgba("#000000", 0);

  // Frame index is the index into this time-sorted copy, which is also what
  // setFrame() receives.
  const frames = [...detail.lens_frames].sort(
    (a, b) => Date.parse(a.t) - Date.parse(b.t),
  );

  const endsData: GeoJSON.FeatureCollection<GeoJSON.Point, EndProperties> = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: detail.off.pos },
        properties: { role: "off" },
      },
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: detail.on.pos },
        properties: { role: "on" },
      },
    ],
  };

  const trackData: GeoJSON.FeatureCollection<GeoJSON.LineString> = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [detail.off.pos, detail.on.pos],
        },
        properties: {},
      },
    ],
  };

  const radarData = (
    frameIndex: number,
  ): GeoJSON.FeatureCollection<GeoJSON.Point, RadarProperties> => ({
    type: "FeatureCollection",
    features: detail.candidates.map((candidate, i) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: candidate.pos },
      properties: {
        index: i,
        // No frames means nothing can be live; null scene times are never live.
        live: frames.length > 0 && isCandidateLive(frames, frameIndex, candidate),
        inside: candidate.inside,
        verdict: candidate.verdict,
      },
    })),
  });

  // Bounds of the footprint polygon, computed here because MapLibre has no
  // built-in GeoJSON extents helper in this version.
  let minLon = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLon = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  for (const ring of detail.footprint.coordinates) {
    for (const [lon, lat] of ring) {
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
  }
  const hasBounds =
    Number.isFinite(minLon) &&
    Number.isFinite(minLat) &&
    Number.isFinite(maxLon) &&
    Number.isFinite(maxLat);

  let frameIndex = 0;
  let ready = false;
  const markers: maplibregl.Marker[] = [];

  const clampFrame = (index: number): number => {
    if (frames.length === 0) return 0;
    const i = Number.isFinite(index) ? Math.trunc(index) : 0;
    return Math.max(0, Math.min(frames.length - 1, i));
  };

  const applyFrame = (index: number): void => {
    const lens = map.getSource("lens") as maplibregl.GeoJSONSource | undefined;
    lens?.setData(polygonData(frames[index]?.polygon ?? null));

    // The likely contour is part of the same frame: scrub both together so the
    // brighter shade never lags the lens it sits inside.
    const likely = map.getSource("likely") as maplibregl.GeoJSONSource | undefined;
    likely?.setData(polygonData(frames[index]?.likely ?? null));

    const radar = map.getSource("radar") as maplibregl.GeoJSONSource | undefined;
    radar?.setData(radarData(index));
  };

  const setFrame = (index: number): void => {
    frameIndex = clampFrame(index);
    // Sources only exist after the offline style has loaded; the load handler
    // applies the stored frame index once they do.
    if (ready) applyFrame(frameIndex);
  };

  map.on("load", () => {
    // Icons must exist before the symbol layer renders its first frame.
    addDiamondImages(map);

    map.addSource("footprint", {
      type: "geojson",
      data: polygonData(detail.footprint),
    });
    map.addSource("lens", { type: "geojson", data: polygonData(null) });
    map.addSource("likely", { type: "geojson", data: polygonData(null) });
    map.addSource("track", { type: "geojson", data: trackData });
    map.addSource("ends", { type: "geojson", data: endsData });
    map.addSource("radar", { type: "geojson", data: radarData(frameIndex) });

    map.addLayer({
      id: "footprint-fill",
      type: "fill",
      source: "footprint",
      paint: {
        "fill-color": prism,
        // Kept invisible: the fill layer anchors the footprint source, while
        // the dotted outline carries the meaning.
        "fill-opacity": 0,
      },
    });

    map.addLayer({
      id: "footprint-line",
      type: "line",
      source: "footprint",
      layout: { "line-join": "round" },
      paint: {
        "line-color": prism,
        "line-width": 1.5,
        "line-opacity": 0.8,
        "line-dasharray": [1, 3],
      },
    });

    map.addLayer({
      id: "lens-fill",
      type: "fill",
      source: "lens",
      paint: {
        "fill-color": prismFill,
      },
    });

    // Brighter violet inside the lens: the bridge's most likely position. No
    // outline on purpose, so it reads as a shade rather than a second boundary.
    map.addLayer({
      id: "likely-fill",
      type: "fill",
      source: "likely",
      paint: {
        "fill-color": likelyFill,
        "fill-outline-color": noFill,
      },
    });

    map.addLayer({
      id: "lens-line",
      type: "line",
      source: "lens",
      layout: { "line-join": "round" },
      paint: {
        "line-color": prism,
        "line-width": 1.5,
      },
    });

    map.addLayer({
      id: "track-line",
      type: "line",
      source: "track",
      layout: { "line-join": "round", "line-cap": "butt" },
      paint: {
        "line-color": amber,
        "line-width": 2,
        "line-dasharray": [6, 5],
      },
    });

    map.addLayer({
      id: "ends-circle",
      type: "circle",
      source: "ends",
      paint: {
        "circle-radius": 4,
        "circle-color": [
          "case",
          ["==", ["get", "role"], "on"],
          noFill,
          amber,
        ],
        "circle-stroke-color": amber,
        "circle-stroke-width": 1.5,
      },
    });

    // Symbol layer: no text fields because the offline style has no glyphs.
    map.addLayer({
      id: "radar-symbols",
      type: "symbol",
      source: "radar",
      layout: {
        "icon-image": [
          "case",
          ["all", ["==", ["get", "live"], true], ["==", ["get", "inside"], true]],
          "diamond-inside",
          ["all", ["==", ["get", "live"], true], ["==", ["get", "inside"], false]],
          "diamond-outside",
          "diamond-radar",
        ],
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
    });

    ready = true;
    applyFrame(frameIndex);

    if (hasBounds) {
      map.fitBounds(
        [
          [minLon, minLat],
          [maxLon, maxLat],
        ],
        { padding: 80, maxZoom: 8, duration: 0 },
      );
    }

    // HTML labels replace a glyph-rendered text layer. Anchor the text above
    // the end dot with a 10 px gap so the dot never sits on top of the words.
    const startLabel = document.createElement("div");
    startLabel.className = "map-label";
    startLabel.textContent = `Last ping ${fmtUtc(detail.off.t)}`;
    markers.push(
      new maplibregl.Marker({
        element: startLabel,
        anchor: "bottom",
        offset: [0, -10],
      })
        .setLngLat(detail.off.pos)
        .addTo(map),
    );

    const endLabel = document.createElement("div");
    endLabel.className = "map-label";
    endLabel.textContent = `Reappears ${fmtUtc(detail.on.t)}`;
    markers.push(
      new maplibregl.Marker({
        element: endLabel,
        anchor: "bottom",
        offset: [0, -10],
      })
        .setLngLat(detail.on.pos)
        .addTo(map),
    );
  });

  setFrame(0);

  const hidden = new Set<CaseLayerGroup>();
  const applyVisibility = (): void => {
    if (!ready) return;
    for (const [group, ids] of Object.entries(GROUP_LAYERS) as [CaseLayerGroup, string[]][]) {
      for (const id of ids) {
        map.setLayoutProperty(id, "visibility", hidden.has(group) ? "none" : "visible");
      }
    }
    for (const marker of markers) {
      marker.getElement().style.display = hidden.has("gap") ? "none" : "";
    }
  };
  map.on("load", applyVisibility);

  return {
    setFrame,
    setLayerVisible(group, visible): void {
      if (visible) hidden.delete(group);
      else hidden.add(group);
      applyVisibility();
    },
    destroy(): void {
      for (const marker of markers) marker.remove();
      markers.length = 0;
      map.remove();
    },
  };
}
