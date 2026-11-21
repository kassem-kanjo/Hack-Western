/**
 * Evidence screen map: the whole world with one dot per case. Amber = we cannot
 * explain it, grey = cleared. Clicking a dot opens the case. Offline style, no tiles.
 */

import * as maplibregl from "maplibre-gl";

import type { CaseSummary } from "../api";
import { offlineStyle } from "../basemap";
import { rgba, themeColor } from "./theme";

export type EvidenceFilter = "all" | "unexplained" | "cleared";

export interface EvidenceMapHandle {
  setFilter(filter: EvidenceFilter): void;
  /** Ring one case (list row hover); null clears it. */
  highlight(id: string | null): void;
  destroy(): void;
}

export function mountEvidenceMap(container: HTMLElement, cases: CaseSummary[]): EvidenceMapHandle {
  const map = new maplibregl.Map({
    container,
    style: offlineStyle(),
    center: [10, 20],
    zoom: 1.4,
    attributionControl: false,
    interactive: true,
  });

  const amber = themeColor("--c-gap", "#FFB020");
  const grey = themeColor("--c-text-3", "#8A9BAD");
  const white = themeColor("--c-text-1", "#EAF0F6");

  // Cleared first so the amber dots sit on top.
  const ordered = [...cases].sort((a, b) => Number(a.status === "candidate") - Number(b.status === "candidate"));
  const data: GeoJSON.FeatureCollection<GeoJSON.Point, { id: string; unexplained: boolean }> = {
    type: "FeatureCollection",
    features: ordered.map((c) => ({
      type: "Feature",
      // The last ping is where the ship went dark.
      geometry: { type: "Point", coordinates: c.off.pos },
      properties: { id: c.id, unexplained: c.status === "candidate" },
    })),
  };

  let ready = false;
  let pending: EvidenceFilter = "all";
  let pendingHighlight: string | null = null;

  const apply = (): void => {
    if (!ready) return;
    const f: maplibregl.FilterSpecification | null =
      pending === "all" ? null : ["==", ["get", "unexplained"], pending === "unexplained"];
    map.setFilter("ev-dots", f);
    map.setFilter("ev-halo", ["==", ["get", "id"], pendingHighlight ?? ""]);
  };

  map.on("load", () => {
    map.addSource("ev", { type: "geojson", data });
    map.addLayer({
      id: "ev-halo",
      type: "circle",
      source: "ev",
      paint: {
        "circle-radius": 11,
        "circle-color": rgba("#000000", 0),
        "circle-stroke-color": white,
        "circle-stroke-width": 1.5,
      },
    });
    map.addLayer({
      id: "ev-dots",
      type: "circle",
      source: "ev",
      paint: {
        "circle-radius": ["case", ["get", "unexplained"], 6, 4],
        "circle-color": ["case", ["get", "unexplained"], amber, rgba(grey, 0.7)],
        "circle-stroke-color": ["case", ["get", "unexplained"], rgba("#000000", 0.6), rgba("#000000", 0.4)],
        "circle-stroke-width": 1,
      },
    });
    ready = true;
    apply();
  });

  map.on("click", "ev-dots", (e) => {
    const id = e.features?.[0]?.properties?.id;
    if (typeof id === "string" && id !== "") window.location.hash = "#/case/" + encodeURIComponent(id);
  });
  map.on("mouseenter", "ev-dots", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "ev-dots", () => {
    map.getCanvas().style.cursor = "";
  });

  return {
    setFilter(filter): void {
      pending = filter;
      apply();
    },
    highlight(id): void {
      pendingHighlight = id;
      apply();
    },
    destroy(): void {
      map.remove();
    },
  };
}
