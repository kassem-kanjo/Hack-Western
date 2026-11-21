/**
 * Live-region map: the dim AIS field, event markers by kind, the one selected
 * event and the cluster bubbles.
 *
 * The basemap comes from basemap.ts and is fully offline: it has no glyph
 * server and no sprite sheet. Nothing here therefore uses text-field or an
 * external image. Event markers are the shapes the design draws (amber ring
 * with a dashed tail, white triangle, twin squares, pink double ring,
 * crosshair), built as SVG in an HTML overlay and positioned with
 * map.project() as the camera moves. The overlay also carries every piece of
 * text (cluster counts, rank numbers, the selected-event label), so the map
 * needs no glyphs and keeps working with no network.
 *
 * The module deliberately knows nothing about lists, chips or the rail: the
 * screen owns those and passes in already-filtered events, in list order.
 */

import * as maplibregl from "maplibre-gl";

import { offlineStyle } from "../basemap";
import { h } from "../components/dom";
import type { EventKind, LiveEvent, LiveVessel } from "../model/live";
import { KIND_LABEL, vesselName } from "../model/liveFacts";
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
  /**
   * events are ALREADY filtered and in list order. selected is one of them, or null.
   * `numbered` is how many leading events carry their list row number (1..n); the
   * rest are drawn smaller and unnumbered.
   */
  setEvents(events: LiveEvent[], selected: LiveEvent | null, numbered?: number): void;
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

/**
 * Short phrase for the selected-event label. The vessel name is prepended by
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

/** How many leading events are numbered; matches the rail's row count. */
const DEFAULT_NUMBERED = 7;

/** Unnumbered markers keep their shape but shrink, so the seven rows stay findable. */
const SMALL_SCALE = 0.72;

/** A silence tail is a pointer, not the whole gap: past this it would cross the map. */
const TAIL_MAX_PX = 46;

/** Click target radius; the shapes themselves are smaller than a comfortable finger. */
const HIT_RADIUS = 14;

/** The white ring around the selected event. */
const SELECT_RADIUS = 15;

/**
 * How far left of the shape's centre the rank number sits, per kind: just
 * outside the widest part of the glyph, as in the design.
 */
const NUMBER_CLEARANCE: Record<EventKind, number> = {
  silence: 15,
  jump: 15,
  clone: 14,
  meeting: 20,
  spoof: 21,
};

/** Zoom for one event; wide enough that the neighbours that make it unusual stay in frame. */
const SELECTED_ZOOM = 7.5;

/**
 * The two places an event is really about, as [lon, lat] points: a silence is
 * the gap from the last fix to the next, a clone is its two clusters. Other
 * kinds happen at one point.
 */
function eventPair(event: LiveEvent): [[number, number], [number, number]] | null {
  if (event.kind === "clone") {
    const a = evidenceTuple(event.evidence, "cluster_a");
    const b = evidenceTuple(event.evidence, "cluster_b");
    return a !== null && b !== null ? [a, b] : null;
  }
  if (event.kind === "silence") {
    const a = evidencePoint(event.evidence, "last_lat", "last_lon");
    const b = evidencePoint(event.evidence, "next_lat", "next_lon");
    return a !== null && b !== null ? [a, b] : null;
  }
  return null;
}

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

const SVG_NS = "http://www.w3.org/2000/svg";

/** A tiny typed wrapper around createElementNS; attributes never become markup. */
function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  return el;
}

/** A finite number from unknown evidence, or null. */
function evidenceNum(evidence: Record<string, unknown>, key: string): number | null {
  const raw = evidence[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/** A [lat, lon] evidence pair (silence fixes, clone clusters), or null. */
function evidencePoint(
  evidence: Record<string, unknown>,
  latKey: string,
  lonKey: string,
): [number, number] | null {
  const lat = evidenceNum(evidence, latKey);
  const lon = evidenceNum(evidence, lonKey);
  if (lat === null || lon === null) return null;
  return [lon, lat];
}

/** A [lat, lon] pair stored as an array (clone clusters, spoof centre), as [lon, lat]. */
function evidenceTuple(evidence: Record<string, unknown>, key: string): [number, number] | null {
  const raw = evidence[key];
  if (!Array.isArray(raw) || raw.length < 2) return null;
  const lat = raw[0];
  const lon = raw[1];
  if (typeof lat !== "number" || !Number.isFinite(lat)) return null;
  if (typeof lon !== "number" || !Number.isFinite(lon)) return null;
  return [lon, lat];
}

/** Everything needed to move one marker after the camera moves. */
interface MarkerRefs {
  event: LiveEvent;
  /** The group holding the glyph and the rank number, translated to `primary`. */
  group: SVGGElement;
  /** [lon, lat] the group is anchored on. */
  primary: [number, number];
  /** Where a tail or connector ends (silence next fix, clone cluster B). */
  secondary: [number, number] | null;
  /** Dashed pointer toward `secondary` (silence only). */
  tail: SVGLineElement | null;
  tailDot: SVGCircleElement | null;
  /** Dotted connector and the second square (clone only). */
  link: SVGLineElement | null;
  squareB: SVGRectElement | null;
  /** Invisible second click target on cluster B (clone only). */
  hitB: SVGCircleElement | null;
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
    /** Marker interiors hide the AIS dots behind a shape, as in the design. */
    sea: themeColor("--c-bg", "#0A1017"),
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
  // the canvas but never takes pointer events itself, so pans still reach the
  // map; only the marker hit shapes re-enable them.
  const overlay = h("div", { class: "live-map-overlay" });
  overlay.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;z-index:1;";

  const labelLayer = h("div", { class: "live-map-labels" });
  labelLayer.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;";
  const markerLayer = h("div", { class: "live-map-markers" });
  markerLayer.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;";
  const selectionLayer = h("div", { class: "live-map-selection" });
  selectionLayer.style.cssText =
    "position:absolute;inset:0;overflow:hidden;pointer-events:none;";

  const svg = svgEl("svg", { class: "live-map-marker-svg", "aria-label": "Event markers" });
  svg.style.cssText = "position:absolute;left:0;top:0;overflow:visible;";
  markerLayer.append(svg);
  // Connectors and tails paint under the shapes; the markers above the labels.
  const lineLayer = svgEl("g", { class: "live-marker-lines" });
  const shapeLayer = svgEl("g", { class: "live-marker-shapes" });
  const selectedRing = svgEl("circle", {
    class: "live-marker-selected",
    cx: 0,
    cy: 0,
    r: SELECT_RADIUS,
    fill: "none",
    stroke: colors.text1,
    "stroke-width": 1.5,
  });
  selectedRing.style.display = "none";
  svg.append(lineLayer, shapeLayer, selectedRing);

  overlay.append(labelLayer, markerLayer, selectionLayer);
  opts.container.append(overlay);
  sizeSvg();

  let ready = false;
  let destroyed = false;

  let vessels: LiveVessel[] = [];
  let events: LiveEvent[] = [];
  let numberedCount = DEFAULT_NUMBERED;
  let selected: LiveEvent | null = null;

  let markers: MarkerRefs[] = [];

  let selectedLabel: HTMLElement | null = null;
  let selectedLngLat: [number, number] | null = null;

  // Cluster id -> label and the cluster's position, so a label can follow the
  // cluster during a pan without re-querying the layer every frame.
  const clusterLabels = new Map<number, HTMLElement>();
  const clusterPoints = new Map<number, [number, number]>();

  function source(id: string): maplibregl.GeoJSONSource | undefined {
    return map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  }

  /** The SVG is the size of the stage, so one CSS px is one SVG unit. */
  function sizeSvg(): void {
    const width = Math.max(0, opts.container.clientWidth);
    const height = Math.max(0, opts.container.clientHeight);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
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

  /** True when a projected point is (nearly) on screen; markers outside are hidden. */
  function onScreen(x: number, y: number): boolean {
    const margin = 40;
    return (
      x >= -margin &&
      y >= -margin &&
      x <= opts.container.clientWidth + margin &&
      y <= opts.container.clientHeight + margin
    );
  }

  /** Reposition every marker and overlay; cheap, so it can run on each rendered frame. */
  function positionOverlays(): void {
    if (destroyed || !ready) return;

    for (const [id, label] of clusterLabels) {
      const lngLat = clusterPoints.get(id);
      if (lngLat === undefined) continue;
      place(label, lngLat, "centre");
    }

    for (const marker of markers) {
      const point = map.project(marker.primary);
      if (!onScreen(point.x, point.y)) {
        marker.group.style.display = "none";
        if (marker.tail !== null) marker.tail.style.display = "none";
        if (marker.tailDot !== null) marker.tailDot.style.display = "none";
        if (marker.link !== null) marker.link.style.display = "none";
        if (marker.squareB !== null) marker.squareB.style.display = "none";
        if (marker.hitB !== null) marker.hitB.style.display = "none";
        continue;
      }

      marker.group.style.display = "";
      marker.group.setAttribute("transform", `translate(${point.x} ${point.y})`);

      if (marker.secondary !== null) {
        const other = map.project(marker.secondary);
        const visibleOther = onScreen(other.x, other.y);

        if (marker.tail !== null && marker.tailDot !== null) {
          const dx = other.x - point.x;
          const dy = other.y - point.y;
          const distance = Math.hypot(dx, dy);
          const ring = 8;
          if (visibleOther && distance > ring + 4) {
            const ux = dx / distance;
            const uy = dy / distance;
            const reach = Math.min(distance - ring, TAIL_MAX_PX);
            const x2 = point.x + ux * (ring + reach);
            const y2 = point.y + uy * (ring + reach);
            marker.tail.style.display = "";
            marker.tailDot.style.display = "";
            marker.tail.setAttribute("x1", String(point.x + ux * ring));
            marker.tail.setAttribute("y1", String(point.y + uy * ring));
            marker.tail.setAttribute("x2", String(x2));
            marker.tail.setAttribute("y2", String(y2));
            marker.tailDot.setAttribute("cx", String(x2));
            marker.tailDot.setAttribute("cy", String(y2));
          } else {
            marker.tail.style.display = "none";
            marker.tailDot.style.display = "none";
          }
        }

        if (marker.link !== null && marker.squareB !== null) {
          if (visibleOther) {
            marker.link.style.display = "";
            marker.squareB.style.display = "";
            marker.link.setAttribute("x1", String(point.x));
            marker.link.setAttribute("y1", String(point.y));
            marker.link.setAttribute("x2", String(other.x));
            marker.link.setAttribute("y2", String(other.y));
            marker.squareB.setAttribute("x", String(other.x - 6));
            marker.squareB.setAttribute("y", String(other.y - 6));
            if (marker.hitB !== null) {
              marker.hitB.style.display = "";
              marker.hitB.setAttribute("cx", String(other.x));
              marker.hitB.setAttribute("cy", String(other.y));
            }
          } else {
            marker.link.style.display = "none";
            marker.squareB.style.display = "none";
            if (marker.hitB !== null) marker.hitB.style.display = "none";
          }
        }
      }
    }

    if (selectedLngLat !== null) {
      const point = map.project(selectedLngLat);
      if (onScreen(point.x, point.y)) {
        selectedRing.style.display = "";
        selectedRing.setAttribute("cx", String(point.x));
        selectedRing.setAttribute("cy", String(point.y));
      } else {
        selectedRing.style.display = "none";
      }
    } else {
      selectedRing.style.display = "none";
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
        labelLayer.append(label);
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

    if (selected === null || !KNOWN_KINDS.has(selected.kind)) {
      positionOverlays();
      return;
    }

    selectedLngLat = [selected.lon, selected.lat];
    const label = h(
      "div",
      { class: "live-event-label" },
      `${vesselName(selected.mmsi, vessels)} ${KIND_PHRASE[selected.kind]}`,
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
    selectionLayer.append(label);
    selectedLabel = label;
  }

  /* ---- markers ---------------------------------------------------------- */

  /** Bind click and keyboard to one marker group; the hit shape carries the cursor. */
  function makeSelectable(group: SVGGElement, event: LiveEvent): void {
    group.setAttribute("role", "button");
    group.setAttribute("tabindex", "0");
    group.setAttribute("aria-label", `${KIND_LABEL[event.kind]}: ${vesselName(event.mmsi, vessels)}`);
    const pick = (): void => {
      if (!destroyed) opts.onSelect(event);
    };
    group.addEventListener("click", (clicked) => {
      clicked.stopPropagation();
      pick();
    });
    group.addEventListener("keydown", (pressed) => {
      if (pressed.key !== "Enter" && pressed.key !== " ") return;
      pressed.preventDefault();
      pick();
    });
  }

  function hitCircle(): SVGCircleElement {
    return svgEl("circle", { cx: 0, cy: 0, r: HIT_RADIUS, class: "live-marker-hit" });
  }

  /**
   * Build one marker. `scale` shrinks the unnumbered markers; the rank number is
   * only drawn for the seven rows the rail lists, matching their 1..7 badges.
   */
  function buildMarker(event: LiveEvent, rank: number | null): MarkerRefs | null {
    if (!KNOWN_KINDS.has(event.kind)) return null;

    const numbered = rank !== null;
    const s = numbered ? 1 : SMALL_SCALE;
    const stroke = (base: number): number => Math.max(1.8, base * s);
    const group = svgEl("g", {
      class: `live-marker is-${event.kind}${numbered ? "" : " is-small"}`,
    });

    let primary: [number, number] = [event.lon, event.lat];
    let secondary: [number, number] | null = null;
    let tail: SVGLineElement | null = null;
    let tailDot: SVGCircleElement | null = null;
    let link: SVGLineElement | null = null;
    let squareB: SVGRectElement | null = null;
    let hitB: SVGCircleElement | null = null;

    if (event.kind === "silence") {
      group.append(
        svgEl("circle", {
          cx: 0,
          cy: 0,
          r: 7 * s,
          fill: colors.sea,
          stroke: colors.gap,
          "stroke-width": stroke(2.5),
        }),
      );
      // The dashed tail points at the next fix the ship reported.
      secondary = evidencePoint(event.evidence, "next_lat", "next_lon");
      if (secondary !== null) {
        tail = svgEl("line", {
          x1: 0,
          y1: 0,
          x2: 0,
          y2: 0,
          stroke: colors.gap,
          "stroke-width": stroke(2),
          "stroke-dasharray": `${Math.round(6 * s)} ${Math.round(5 * s)}`,
          "stroke-linecap": "round",
        });
        tailDot = svgEl("circle", { cx: 0, cy: 0, r: 3.2 * s, fill: colors.gap });
        lineLayer.append(tail, tailDot);
      }
    } else if (event.kind === "jump") {
      const top = -8 * s;
      const bottom = 7 * s;
      const half = 7.5 * s;
      group.append(
        svgEl("path", {
          d: `M0 ${top} L${half} ${bottom} L${-half} ${bottom} Z`,
          fill: colors.sea,
          stroke: colors.candidate,
          "stroke-width": stroke(2.2),
          "stroke-linejoin": "round",
        }),
      );
    } else if (event.kind === "clone") {
      const clusterA = evidenceTuple(event.evidence, "cluster_a");
      const clusterB = evidenceTuple(event.evidence, "cluster_b");
      const size = 12 * s;

      if (clusterA !== null && clusterB !== null) {
        // Two clusters: one square per place, a dotted hop between them.
        primary = clusterA;
        secondary = clusterB;
        group.append(
          svgEl("rect", {
            x: -size / 2,
            y: -size / 2,
            width: size,
            height: size,
            fill: colors.sea,
            stroke: colors.candidate,
            "stroke-width": stroke(2.2),
          }),
        );
        link = svgEl("line", {
          x1: 0,
          y1: 0,
          x2: 0,
          y2: 0,
          stroke: colors.candidate,
          "stroke-width": stroke(1.5),
          "stroke-dasharray": "2 4",
        });
        lineLayer.append(link);
        squareB = svgEl("rect", {
          x: 0,
          y: 0,
          width: size,
          height: size,
          fill: colors.sea,
          stroke: colors.candidate,
          "stroke-width": stroke(2.2),
        });
        shapeLayer.append(squareB);
        hitB = svgEl("circle", {
          cx: 0,
          cy: 0,
          r: HIT_RADIUS,
          class: "live-marker-hit",
        });
        shapeLayer.append(hitB);
      } else {
        // No clusters in evidence: the glyph's twin squares, centred on the point.
        const square = 10 * s;
        const corner = 7.5 * s;
        group.append(
          svgEl("rect", {
            x: -corner,
            y: -corner,
            width: square,
            height: square,
            fill: colors.sea,
            stroke: colors.candidate,
            "stroke-width": stroke(2.2),
          }),
          svgEl("rect", {
            x: -corner + square / 2,
            y: -corner + square / 2,
            width: square,
            height: square,
            fill: colors.sea,
            stroke: colors.candidate,
            "stroke-width": stroke(2.2),
          }),
        );
      }
    } else if (event.kind === "meeting") {
      group.append(
        svgEl("circle", {
          cx: 0,
          cy: 0,
          r: 12 * s,
          fill: rgba(colors.rdv, 0.18),
          stroke: colors.rdv,
          "stroke-width": stroke(2.2),
        }),
        svgEl("circle", {
          cx: 0,
          cy: 0,
          r: 6 * s,
          fill: "none",
          stroke: colors.rdv,
          "stroke-width": stroke(2.2),
        }),
      );
    } else {
      // Spoof: crosshair around the false point.
      group.append(
        svgEl("circle", {
          cx: 0,
          cy: 0,
          r: 6.5 * s,
          fill: colors.sea,
          stroke: colors.candidate,
          "stroke-width": stroke(2.2),
        }),
      );
      const inner = 9.5 * s;
      const outer = 13.5 * s;
      for (const [x1, y1, x2, y2] of [
        [0, -inner, 0, -outer],
        [0, inner, 0, outer],
        [-inner, 0, -outer, 0],
        [inner, 0, outer, 0],
      ] as const) {
        group.append(
          svgEl("line", {
            x1,
            y1,
            x2,
            y2,
            stroke: colors.candidate,
            "stroke-width": stroke(2),
            "stroke-linecap": "round",
          }),
        );
      }
    }

    if (rank !== null) {
      const text = svgEl("text", {
        x: -NUMBER_CLEARANCE[event.kind] - 2,
        y: 4,
        "text-anchor": "end",
        class: "live-marker-num",
      });
      text.textContent = String(rank);
      group.append(text);
    }

    group.append(hitCircle());
    makeSelectable(group, event);
    shapeLayer.append(group);

    if (hitB !== null) {
      const secondaryHit = hitB;
      secondaryHit.setAttribute("role", "button");
      secondaryHit.setAttribute("tabindex", "-1");
      secondaryHit.setAttribute("aria-hidden", "true");
      secondaryHit.addEventListener("click", (clicked) => {
        clicked.stopPropagation();
        if (!destroyed) opts.onSelect(event);
      });
    }

    return {
      event,
      group,
      primary,
      secondary,
      tail,
      tailDot,
      link,
      squareB,
      hitB,
    };
  }

  /** True when the two arrays hold the same objects in the same order. */
  function sameEvents(a: LiveEvent[], b: LiveEvent[]): boolean {
    if (a === b) return true;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
    return true;
  }

  /** Drop every marker node and its connectors; the SVG groups stay. */
  function clearMarkers(): void {
    lineLayer.replaceChildren();
    shapeLayer.replaceChildren();
    markers = [];
  }

  function rebuildMarkers(): void {
    clearMarkers();
    if (!ready) return;
    events.forEach((event, index) => {
      const rank = index < numberedCount ? index + 1 : null;
      const marker = buildMarker(event, rank);
      if (marker !== null) markers.push(marker);
    });
    positionOverlays();
  }

  function setVessels(next: LiveVessel[]): void {
    if (destroyed) return;
    vessels = next;
    if (ready) source("vessels")?.setData(vesselData(vessels));
    updateSelectedLabel();
  }

  function setEvents(next: LiveEvent[], nextSelected: LiveEvent | null, numbered?: number): void {
    if (destroyed) return;
    const count = numbered === undefined ? DEFAULT_NUMBERED : Math.max(0, numbered);
    const needsRebuild =
      count !== numberedCount || !sameEvents(events, next) || markers.length === 0;
    events = next;
    numberedCount = count;
    selected = nextSelected;
    if (needsRebuild) rebuildMarkers();
    updateSelectedLabel();
    positionOverlays();
  }

  map.on("load", () => {
    if (destroyed) return;

    map.addSource("vessels", {
      type: "geojson",
      data: vesselData(vessels),
      cluster: true,
      clusterRadius: 28,
      clusterMaxZoom: 9,
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

    ready = true;

    // First fit never animates; the constructor already set the same view.
    map.fitBounds(view, { padding: 40, duration: 0 });

    sizeSvg();
    rebuildMarkers();
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
      const duration = prefersReducedMotion() ? 0 : 900;
      const pair = eventPair(event);
      if (pair !== null) {
        // Fit both places: a clone's twin squares and a silence's gap only read
        // as one story when both ends are on screen.
        const [[lonA, latA], [lonB, latB]] = pair;
        const bounds: Bounds = [
          [Math.min(lonA, lonB), Math.min(latA, latB)],
          [Math.max(lonA, lonB), Math.max(latA, latB)],
        ];
        map.fitBounds(bounds, {
          padding: 100,
          maxZoom: SELECTED_ZOOM,
          duration,
        });
        return;
      }

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
      sizeSvg();
      positionOverlays();
      syncClusterLabels();
    },

    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      clusterLabels.clear();
      clusterPoints.clear();
      markers = [];
      selectedLabel = null;
      overlay.remove();
      // Removing the map also detaches every listener registered on it.
      map.remove();
    },
  };
}
