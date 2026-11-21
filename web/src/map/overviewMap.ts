/**
 * Overview screen map: every dark gap as one dashed segment, from the last AIS
 * ping to the reappearance point.
 *
 * Offline by design: the style comes from basemap.ts (local land GeoJSON, no
 * remote tiles). Colours are read from the CSS tokens at mount time.
 */

import * as maplibregl from "maplibre-gl";

import type { CaseStatus, CaseSummary } from "../api";
import { offlineStyle } from "../basemap";
import { rgba, themeColor } from "./theme";

export interface OverviewMapHandle {
  map: maplibregl.Map;
  destroy(): void;
}

/** Candidate gaps are drawn last so they sit above ruled-out ones. */
const STATUS_ORDER: Record<CaseStatus, number> = {
  ruled_out: 0,
  plain: 1,
  candidate: 2,
};

interface GapProperties {
  id: string;
  status: CaseStatus;
}

interface EndProperties extends GapProperties {
  role: "off" | "on";
}

export function mountOverviewMap(
  container: HTMLElement,
  cases: CaseSummary[],
): OverviewMapHandle {
  const map = new maplibregl.Map({
    container,
    style: offlineStyle(),
    center: [19, 58],
    zoom: 4.5,
    attributionControl: false,
    interactive: true,
  });

  const amber = themeColor("--c-gap", "#FFB020");
  const grey = themeColor("--c-text-3", "#8A9BAD");
  const greyDim = rgba(grey, 0.55);
  const noFill = rgba("#000000", 0);

  const ordered = [...cases].sort(
    (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status],
  );

  const gaps: GeoJSON.Feature<GeoJSON.LineString, GapProperties>[] = ordered.map(
    (c) => ({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [c.off.pos, c.on.pos],
      },
      properties: { id: c.id, status: c.status },
    }),
  );

  const ends: GeoJSON.Feature<GeoJSON.Point, EndProperties>[] = [];
  for (const c of ordered) {
    ends.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: c.off.pos },
      properties: { id: c.id, status: c.status, role: "off" },
    });
    ends.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: c.on.pos },
      properties: { id: c.id, status: c.status, role: "on" },
    });
  }

  const gapsData: GeoJSON.FeatureCollection<GeoJSON.LineString, GapProperties> = {
    type: "FeatureCollection",
    features: gaps,
  };
  const endsData: GeoJSON.FeatureCollection<GeoJSON.Point, EndProperties> = {
    type: "FeatureCollection",
    features: ends,
  };

  // Fit to every endpoint of every gap. maxZoom stops a single tight case from
  // filling the whole screen.
  const bounds = new maplibregl.LngLatBounds();
  for (const c of cases) {
    bounds.extend(c.off.pos);
    bounds.extend(c.on.pos);
  }

  map.on("load", () => {
    map.addSource("gaps", { type: "geojson", data: gapsData });
    map.addSource("gap-ends", { type: "geojson", data: endsData });

    map.addLayer({
      id: "gaps-line",
      type: "line",
      source: "gaps",
      layout: { "line-join": "round", "line-cap": "butt" },
      paint: {
        "line-color": [
          "case",
          ["==", ["get", "status"], "candidate"],
          amber,
          greyDim,
        ],
        "line-width": 2,
        "line-dasharray": [6, 5],
      },
    });

    map.addLayer({
      id: "gap-ends-circle",
      type: "circle",
      source: "gap-ends",
      paint: {
        // Off is filled, on is hollow. Ruled-out cases use the muted grey.
        "circle-radius": 4,
        "circle-color": [
          "case",
          ["==", ["get", "role"], "on"],
          noFill,
          ["==", ["get", "status"], "candidate"],
          amber,
          grey,
        ],
        "circle-stroke-color": [
          "case",
          ["==", ["get", "status"], "candidate"],
          amber,
          grey,
        ],
        "circle-stroke-width": 1.5,
      },
    });

    if (cases.length > 0) {
      map.fitBounds(bounds, { padding: 60, maxZoom: 6 });
    }
  });

  const openCase = (e: maplibregl.MapLayerMouseEvent): void => {
    const id = e.features?.[0]?.properties?.id;
    if (typeof id !== "string" || id === "") return;
    window.location.hash = "#/case/" + encodeURIComponent(id);
  };

  const setPointer = (): void => {
    map.getCanvas().style.cursor = "pointer";
  };
  const clearPointer = (): void => {
    map.getCanvas().style.cursor = "";
  };

  for (const layerId of ["gaps-line", "gap-ends-circle"]) {
    map.on("click", layerId, openCase);
    map.on("mouseenter", layerId, setPointer);
    map.on("mouseleave", layerId, clearPointer);
  }

  return {
    map,
    destroy(): void {
      map.remove();
    },
  };
}
