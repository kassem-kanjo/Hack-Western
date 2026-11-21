/**
 * Live-region map: the dim AIS field, event markers by kind, the one selected
 * event and the cluster bubbles.
 *
 * The basemap comes from basemap.ts and is fully offline: it has no glyph
 * server and no sprite sheet. Nothing here therefore uses text-field or an
 * external image. Marker icons are drawn once into a canvas and registered with
 * addImage, and all text (cluster counts, the selected-event label) is an HTML
 * overlay positioned with map.project() and refreshed as the camera moves.
 *
 * The module deliberately knows nothing about lists, chips or the rail: the
 * screen owns those and passes in already-filtered events.
 */

import * as maplibregl from "maplibre-gl";

import { offlineStyle } from "../basemap";
import { h } from "../components/dom";
import type { EventKind, LiveEvent, LiveVessel } from "../model/live";
import { rgba, themeColor } from "./theme";

type Bounds = [[number, number], [number, number]];

/** Fallback for an unknown region name. */
const BALTIC_VIEW: Bounds = [
  [9, 53.5],
  [31, 61],
];

/** [[west, south], [east, north]] per region. Unknown regions fall back to BALTIC_WATCH. */
export const REGION_VIEWS: Record<string, [[number, number], [number, number]]> = {
  BALTIC_WATCH: BALTIC_VIEW,
  MALAYSIA_EOPL: [
    [101.5, 1.0],
    [106.0, 6.0],
  ],
  BLACK_SEA: [
    [27, 40.5],
    [42, 47],
  ],
};

export interface LiveMapOptions {
  container: HTMLElement;
  region: string;
  onSelect: (event: LiveEvent) => void; // called when a marker is clicked
}

export interface LiveMapHandle {
  setVessels(vessels: LiveVessel[]): void;
  /** events are ALREADY filtered by the caller. selected is one of them, or null. */
  setEvents(events: LiveEvent[], selected: LiveEvent | null): void;
  /** Zoom to the event (zoom 8 to 9 around its position). Use jumpTo under reduced motion. */
  focusEvent(event: LiveEvent): void;
  /** Back to the region's REGION_VIEWS extent. */
  resetView(): void;
  resize(): void;
  destroy(): void;
}

interface VesselProperties {
  mmsi: number;
}

interface EventProperties {
  kind: EventKind;
  key: number;
}

/**
 * Short phrase for the selected-event label. The MMSI is prepended by
 * updateSelectedLabel(); the record also acts as the set of kinds the map knows
 * how to draw.
 */
const KIND_PHRASE: Record<EventKind, string> = {
  silence: "went silent here",
  jump: "impossible jump",
  clone: "identity clone",
  meeting: "met another ship",
  spoof: "false position",
};

const KNOWN_KINDS = new Set<string>(Object.keys(KIND_PHRASE));

/** Icons are authored in a 24 CSS px box and rasterised at 2x for sharp edges. */
const ICON_SIZE = 24;
const ICON_SCALE = 2;
/** One px of dark halo on each side, so a white marker reads on a pale sea. */
const HALO = "#0A1017";
const HALO_WIDTH = 1;

const SELECTED_ZOOM = 8.5;

/** MapLibre honours this itself for flyTo, but fitBounds/jump need the check. */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** A point per vessel that has a last fix; vessels without one are skipped. */
function vesselData(
  vessels: LiveVessel[],
): GeoJSON.FeatureCollection<GeoJSON.Point, VesselProperties> {
  const features: GeoJSON.Feature<GeoJSON.Point, VesselProperties>[] = [];
  for (const vessel of vessels) {
    const last = vessel.last;
    if (last === null) continue;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [last.lon, last.lat] },
      properties: { mmsi: vessel.mmsi },
    });
  }
  return { type: "FeatureCollection", features };
}

/**
 * Event points. The feature keeps only the index into the caller's array; the
 * original LiveEvent object is looked up there on click, so onSelect always
 * receives the object the caller passed in (not a lossy GeoJSON copy).
 */
function eventData(
  events: LiveEvent[],
): GeoJSON.FeatureCollection<GeoJSON.Point, EventProperties> {
  const features: GeoJSON.Feature<GeoJSON.Point, EventProperties>[] = [];
  events.forEach((event, key) => {
    // Unknown kinds draw nothing. In particular a meeting only ever marks the
    // event's own position; its other_mmsi counterpart has no coordinate here.
    if (!KNOWN_KINDS.has(event.kind)) return;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [event.lon, event.lat] },
      properties: { kind: event.kind, key },
    });
  });
  return { type: "FeatureCollection", features };
}

type Ctx = CanvasRenderingContext2D;

/** Render one icon into a 48 x 48 ImageData (24 CSS px at 2x). */
function renderIcon(draw: (ctx: Ctx) => void): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = ICON_SIZE * ICON_SCALE;
  canvas.height = ICON_SIZE * ICON_SCALE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas context unavailable");
  ctx.scale(ICON_SCALE, ICON_SCALE);
  // getImageData reads device pixels, so the scale only affects drawing.
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

/**
 * Stroke a path twice: a wider dark halo underneath, then the coloured line.
 * Building the path twice keeps dashes and joins identical in both passes.
 */
function outlinedStroke(
  ctx: Ctx,
  path: () => void,
  color: string,
  width: number,
): void {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.setLineDash([]);

  path();
  ctx.strokeStyle = HALO;
  ctx.lineWidth = width + HALO_WIDTH * 2;
  ctx.stroke();

  path();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function circlePath(ctx: Ctx, cx: number, cy: number, radius: number): void {
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
}

function linePath(
  ctx: Ctx,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
}

function rectPath(ctx: Ctx, x: number, y: number, size: number): void {
  ctx.beginPath();
  ctx.rect(x, y, size, size);
}

/**
 * Register the five kind icons on the style. They are generated locally with a
 * canvas, so the map needs no sprite and keeps working offline.
 */
function addEventImages(map: maplibregl.Map, colors: Record<string, string>): void {
  const silence = renderIcon((ctx) => {
    // Ring plus a dashed tail heading down-right.
    outlinedStroke(ctx, () => circlePath(ctx, 8.5, 8.5, 4.5), colors.gap, 2);
    const dashes: [number, number, number, number][] = [
      [12.5, 12.5, 14.5, 14.5],
      [15.8, 15.8, 17.3, 17.3],
      [18.6, 18.6, 20, 20],
    ];
    for (const [x1, y1, x2, y2] of dashes) {
      outlinedStroke(ctx, () => linePath(ctx, x1, y1, x2, y2), colors.gap, 2);
    }
  });

  const jump = renderIcon((ctx) => {
    // Impossible speed: an alert triangle, outline only.
    outlinedStroke(
      ctx,
      () => {
        ctx.beginPath();
        ctx.moveTo(12, 4.5);
        ctx.lineTo(20, 19.5);
        ctx.lineTo(4, 19.5);
        ctx.closePath();
      },
      colors.candidate,
      2,
    );
  });

  const clone = renderIcon((ctx) => {
    // Identity clone: two outline squares offset diagonally.
    outlinedStroke(ctx, () => rectPath(ctx, 4, 4, 9), colors.candidate, 2);
    outlinedStroke(ctx, () => rectPath(ctx, 11, 11, 9), colors.candidate, 2);
  });

  const meeting = renderIcon((ctx) => {
    // Rendezvous: two concentric pink rings.
    outlinedStroke(ctx, () => circlePath(ctx, 12, 12, 7), colors.rdv, 2);
    outlinedStroke(ctx, () => circlePath(ctx, 12, 12, 3.5), colors.rdv, 2);
  });

  const spoof = renderIcon((ctx) => {
    // False position: a crosshair, small ring plus four ticks.
    outlinedStroke(ctx, () => circlePath(ctx, 12, 12, 4), colors.candidate, 2);
    const ticks: [number, number, number, number][] = [
      [12, 6.5, 12, 3],
      [12, 17.5, 12, 21],
      [6.5, 12, 3, 12],
      [17.5, 12, 21, 12],
    ];
    for (const [x1, y1, x2, y2] of ticks) {
      outlinedStroke(
        ctx,
        () => linePath(ctx, x1, y1, x2, y2),
        colors.candidate,
        2,
      );
    }
  });

  const images: [EventKind, ImageData][] = [
    ["silence", silence],
    ["jump", jump],
    ["clone", clone],
    ["meeting", meeting],
    ["spoof", spoof],
  ];

  for (const [kind, image] of images) {
    if (map.hasImage(kind)) continue;
    // No pixelRatio option: the ImageData is 48 px, i.e. 2x the 24 px design,
    // and the layer's icon-size 0.5 brings it back to 24 CSS px.
    map.addImage(kind, image);
  }
}

export function mountLiveMap(opts: LiveMapOptions): LiveMapHandle {
  const view = REGION_VIEWS[opts.region] ?? BALTIC_VIEW;

  const colors = {
    ais: themeColor("--c-ais", "#4FD1B0"),
    gap: themeColor("--c-gap", "#FFB020"),
    candidate: themeColor("--c-candidate", "#F4FAFF"),
    rdv: themeColor("--c-rdv", "#FF4F9A"),
    text1: themeColor("--c-text-1", "#EAF0F6"),
    text2: themeColor("--c-text-2", "#B2BFCD"),
    lineStrong: themeColor("--c-line-strong", "#34485F"),
    panel: themeColor("--c-panel", "#101923"),
  };

  const map = new maplibregl.Map({
    container: opts.container,
    style: offlineStyle(),
    // Fitting here avoids a world-view flash before the load handler runs.
    bounds: view,
    fitBoundsOptions: { padding: 40, animate: false },
    attributionControl: false,
    interactive: true,
  });

  // HTML overlay for everything the glyph-less style cannot draw. It sits over
  // the canvas but never takes pointer events, so pans and marker clicks still
  // reach the map.
  const overlay = h("div", { class: "live-map-overlay" });
  overlay.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;z-index:1;";
  opts.container.append(overlay);

  let ready = false;
  let destroyed = false;

  let vessels: LiveVessel[] = [];
  let events: LiveEvent[] = [];
  let selected: LiveEvent | null = null;

  let selectedLabel: HTMLElement | null = null;
  let selectedLngLat: [number, number] | null = null;

  // Cluster id -> label and the cluster's position, so a label can follow the
  // cluster during a pan without re-querying the layer every frame.
  const clusterLabels = new Map<number, HTMLElement>();
  const clusterPoints = new Map<number, [number, number]>();

  function source(id: string): maplibregl.GeoJSONSource | undefined {
    return map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  }

  function selectedData(): GeoJSON.FeatureCollection<GeoJSON.Point> {
    if (selected === null || !KNOWN_KINDS.has(selected.kind)) {
      return { type: "FeatureCollection", features: [] };
    }
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Point",
            coordinates: [selected.lon, selected.lat],
          },
          properties: {},
        },
      ],
    };
  }

  function place(
    element: HTMLElement,
    lngLat: [number, number],
    anchor: "centre" | "above",
  ): void {
    const point = map.project(lngLat);
    const width = opts.container.clientWidth;
    const height = opts.container.clientHeight;
    const margin = 60;
    if (
      point.x < -margin ||
      point.x > width + margin ||
      point.y < -margin ||
      point.y > height + margin
    ) {
      element.style.visibility = "hidden";
      return;
    }

    element.style.visibility = "visible";
    const y = anchor === "above" ? point.y - 14 : point.y;
    const shift =
      anchor === "above" ? " translate(-50%, -100%)" : " translate(-50%, -50%)";
    element.style.transform = `translate(${point.x}px, ${y}px)${shift}`;
  }

  /** Reposition every overlay; cheap, so it can run on each rendered frame. */
  function positionOverlays(): void {
    if (destroyed) return;
    for (const [id, label] of clusterLabels) {
      const lngLat = clusterPoints.get(id);
      if (lngLat === undefined) continue;
      place(label, lngLat, "centre");
    }
    if (selectedLabel !== null && selectedLngLat !== null) {
      place(selectedLabel, selectedLngLat, "above");
    }
  }

  /**
   * Rebuild the cluster labels from what is actually rendered. queryRendered-
   * Features only reports clusters inside the viewport, which is exactly the
   * "one label per visible cluster" rule.
   */
  function syncClusterLabels(): void {
    if (!ready || destroyed) return;

    const features = map.queryRenderedFeatures({ layers: ["vessel-clusters"] });
    const seen = new Set<number>();

    for (const feature of features) {
      if (feature.geometry.type !== "Point") continue;
      const properties = feature.properties as {
        cluster_id?: unknown;
        point_count?: unknown;
      };
      const id = Number(properties.cluster_id);
      const count = Number(properties.point_count);
      if (!Number.isFinite(id) || !Number.isFinite(count)) continue;
      if (seen.has(id)) continue;

      const [lng, lat] = feature.geometry.coordinates;
      if (typeof lng !== "number" || typeof lat !== "number") continue;

      seen.add(id);
      clusterPoints.set(id, [lng, lat]);

      let label = clusterLabels.get(id);
      if (label === undefined) {
        label = h("div", { class: "live-cluster-count" });
        label.style.cssText = [
          "position:absolute",
          "left:0",
          "top:0",
          `color:${colors.text2}`,
          "font-family:var(--font-mono)",
          "font-size:10px",
          "font-weight:500",
          "line-height:1",
          "white-space:nowrap",
          "pointer-events:none",
          "visibility:hidden",
          "text-shadow:0 1px 2px #0A1017",
          "will-change:transform",
        ].join(";");
        overlay.append(label);
        clusterLabels.set(id, label);
      }
      label.textContent = String(count);
    }

    for (const [id, label] of clusterLabels) {
      if (seen.has(id)) continue;
      label.remove();
      clusterLabels.delete(id);
      clusterPoints.delete(id);
    }

    positionOverlays();
  }

  /** (Re)build the selected-event label, which is DOM rather than text-field. */
  function updateSelectedLabel(): void {
    if (selectedLabel !== null) {
      selectedLabel.remove();
      selectedLabel = null;
    }
    selectedLngLat = null;

    if (selected === null || !KNOWN_KINDS.has(selected.kind)) return;

    selectedLngLat = [selected.lon, selected.lat];
    const label = h(
      "div",
      { class: "live-event-label" },
      `${selected.mmsi} ${KIND_PHRASE[selected.kind]}`,
    );
    label.style.cssText = [
      "position:absolute",
      "left:0",
      "top:0",
      `color:${colors.text1}`,
      `background:${rgba(colors.panel, 0.92)}`,
      `border:1px solid ${colors.lineStrong}`,
      "border-radius:2px",
      "padding:3px 6px",
      "font-family:var(--font-sans)",
      "font-size:11px",
      "line-height:1.2",
      "white-space:nowrap",
      "pointer-events:none",
      "visibility:hidden",
      "z-index:2",
      "will-change:transform",
    ].join(";");
    overlay.append(label);
    selectedLabel = label;
  }

  function setVessels(next: LiveVessel[]): void {
    if (destroyed) return;
    vessels = next;
    if (ready) source("vessels")?.setData(vesselData(vessels));
  }

  function setEvents(next: LiveEvent[], nextSelected: LiveEvent | null): void {
    if (destroyed) return;
    events = next;
    selected = nextSelected;
    updateSelectedLabel();
    if (ready) {
      source("events")?.setData(eventData(events));
      source("event-selected")?.setData(selectedData());
    }
    positionOverlays();
  }

  map.on("load", () => {
    if (destroyed) return;

    addEventImages(map, colors);

    map.addSource("vessels", {
      type: "geojson",
      data: vesselData(vessels),
      cluster: true,
      clusterRadius: 28,
      clusterMaxZoom: 9,
    });

    map.addSource("events", {
      type: "geojson",
      data: eventData(events),
    });

    map.addSource("event-selected", {
      type: "geojson",
      data: selectedData(),
    });

    // Dim field: one small dot per vessel, drawn first so markers sit above it.
    map.addLayer({
      id: "vessel-points",
      type: "circle",
      source: "vessels",
      filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-radius": 1.4,
        "circle-color": colors.ais,
        "circle-opacity": 0.5,
      },
    });

    map.addLayer({
      id: "vessel-clusters",
      type: "circle",
      source: "vessels",
      filter: ["has", "point_count"],
      paint: {
        "circle-color": colors.ais,
        "circle-opacity": 0.18,
        "circle-stroke-color": colors.ais,
        "circle-stroke-width": 1,
        "circle-radius": [
          "step",
          ["get", "point_count"],
          10,
          100,
          16,
          1000,
          22,
        ],
      },
    });

    // Symbol layer, not text: the kind is the image id. The offline style has
    // no glyphs, so a text-field would render nothing at all.
    map.addLayer({
      id: "event-markers",
      type: "symbol",
      source: "events",
      layout: {
        "icon-image": ["get", "kind"],
        "icon-allow-overlap": true,
        "icon-size": 0.5,
      },
    });

    // One ring, on the selected point only (the source holds at most one).
    map.addLayer({
      id: "event-selected-ring",
      type: "circle",
      source: "event-selected",
      paint: {
        "circle-radius": 11,
        "circle-color": rgba(colors.candidate, 0),
        "circle-stroke-color": colors.candidate,
        "circle-stroke-width": 2,
      },
    });

    ready = true;

    // First fit never animates; the constructor already set the same view.
    map.fitBounds(view, { padding: 40, duration: 0 });

    map.on("click", "event-markers", (event: maplibregl.MapLayerMouseEvent) => {
      const key = event.features?.[0]?.properties?.key;
      if (typeof key !== "number" || !Number.isInteger(key) || key < 0) return;
      const picked: LiveEvent | undefined = events[key];
      if (picked !== undefined) opts.onSelect(picked);
    });

    const showPointer = (): void => {
      map.getCanvas().style.cursor = "pointer";
    };
    const hidePointer = (): void => {
      map.getCanvas().style.cursor = "";
    };
    map.on("mouseenter", "event-markers", showPointer);
    map.on("mouseleave", "event-markers", hidePointer);

    syncClusterLabels();
  });

  // Existing labels follow the camera every frame; new or disappeared clusters
  // are reconciled once the camera settles.
  map.on("render", positionOverlays);
  map.on("move", positionOverlays);
  map.on("moveend", syncClusterLabels);
  map.on("idle", syncClusterLabels);

  return {
    setVessels,
    setEvents,

    focusEvent(event: LiveEvent): void {
      if (destroyed) return;
      const center: [number, number] = [event.lon, event.lat];
      if (prefersReducedMotion()) {
        map.jumpTo({ center, zoom: SELECTED_ZOOM });
      } else {
        map.flyTo({ center, zoom: SELECTED_ZOOM, essential: true });
      }
    },

    resetView(): void {
      if (destroyed) return;
      map.fitBounds(view, {
        padding: 40,
        duration: prefersReducedMotion() ? 0 : 600,
      });
    },

    resize(): void {
      if (destroyed) return;
      map.resize();
      positionOverlays();
      syncClusterLabels();
    },

    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      clusterLabels.clear();
      clusterPoints.clear();
      selectedLabel = null;
      overlay.remove();
      // Removing the map also detaches every listener registered on it.
      map.remove();
    },
  };
}
