/**
 * The two Hormuz story cards (#219) and their map drawing. Every number is read from
 * /api/hormuz/stories; the only constant is the strike date the card measures days from.
 * The cards claim no cargo and no meetings.
 */

import { Marker, type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl";

import type { HormuzCardKey } from "../present/cardRequest";
import { fmtInt } from "../format";
import { h } from "./dom";

const SOURCE = "hormuz-stories";
const STRIKE_DAY = Date.parse("2026-02-28T00:00:00Z");
const DAY_MS = 86_400_000;
const CIRCLE_POINTS = 72;

export interface Point {
  lat: number;
  lon: number;
}

export interface StoryListed {
  mmsi: string;
  name: string | null;
  lists: string[];
  start: string;
  hours: number;
  off: Point;
  on: Point;
  moved_km: number | null;
  reach_km: number;
  closer_to_strait_km: number | null;
}

export interface StoryCluster {
  day: string;
  start: string;
  vessels: number;
  span_hours: number;
  median_hours: number;
  max_km: number;
  median_kn: number | null;
  min_kn: number | null;
  raised_cap: number;
  points: { off: Point; on: Point }[];
  centre: Point;
}

export interface HormuzStories {
  source?: string;
  listed: StoryListed[];
  clusters: StoryCluster[];
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * The offline basemap has no text glyphs, so every label is a DOM marker. They sit outside
 * the GeoJSON source and must be torn down by hand whenever the drawn card changes.
 */
let labels: Marker[] = [];

function clearLabels(): void {
  for (const marker of labels) marker.remove();
  labels = [];
}

/**
 * The two story anchors are fixed to their places, not to the open card: they are how the
 * first view explains itself before anything is clicked, so they survive every card change.
 * They live in their own list because clearLabels() only tears down card labels.
 */
let anchors: Marker[] = [];

/** Remove the fixed story anchors; the screen calls this on exit. */
export function clearStoryAnchors(): void {
  for (const marker of anchors) marker.remove();
  anchors = [];
}

/** A story anchor is a button: a click asks for its card, and a second click closes it. */
function anchorLabel(
  map: MapLibreMap,
  text: string,
  key: HormuzCardKey,
  at: Point,
  onPick: (key: HormuzCardKey) => void,
): void {
  const element = h("button", { class: "hz-anchor", type: "button" }, text);
  element.addEventListener("click", () => onPick(key));
  // The offset lifts the label off its dot. The corner anchor points the label inward from
  // whichever half of the frame it sits in, so an edge label does not run off the map.
  const anchor = at.lon >= map.getCenter().lng ? "bottom-right" : "bottom-left";
  anchors.push(new Marker({ element, anchor, offset: [0, -8] }).setLngLat([at.lon, at.lat]).addTo(map));
}

/**
 * Put the two story places on the map before any card is open: where the lead ship switched
 * off, and where the cluster came back. Every number and date is read from the stories.
 */
export function addStoryAnchors(
  map: MapLibreMap,
  stories: HormuzStories,
  onPick: (key: HormuzCardKey) => void,
): void {
  clearStoryAnchors();
  const listed = leadListed(stories);
  if (listed !== null) {
    const name = listed.name ?? `MMSI ${listed.mmsi}`;
    const text = `${name} switched off here, ${dayLabel(listed.start.slice(0, 10))}`;
    anchorLabel(map, text, "sea1", listed.off, onPick);
  }
  const cluster = leadCluster(stories);
  if (cluster !== null) {
    const text = `${cluster.vessels} ships 'came back' here, ${dayLabel(cluster.day)}`;
    anchorLabel(map, text, "cluster", cluster.centre, onPick);
  }
}

/** Place an HTML label as a map marker; the vertical offset keeps it off the dot or circle edge. */
function placeLabel(
  map: MapLibreMap,
  element: HTMLElement,
  at: Point,
  anchor: "top" | "bottom",
  offsetY: number,
): void {
  labels.push(new Marker({ element, anchor, offset: [0, offsetY] }).setLngLat([at.lon, at.lat]).addTo(map));
}

/** An arrowhead at the gap line's midpoint, pointing from off to on. */
function addArrow(map: MapLibreMap, off: Point, on: Point): void {
  // Measure the angle in Mercator, so it matches the straight line the map draws at any zoom.
  const mercatorY = (lat: number): number => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
  const angle = (Math.atan2(-(mercatorY(on.lat) - mercatorY(off.lat)), on.lon - off.lon) * 180) / Math.PI;
  const head = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  head.setAttribute("class", "hz-map-arrow");
  head.setAttribute("width", "10");
  head.setAttribute("height", "10");
  head.setAttribute("viewBox", "0 0 10 10");
  head.setAttribute("aria-hidden", "true");
  head.style.transform = `rotate(${angle.toFixed(1)}deg)`;
  const tip = document.createElementNS("http://www.w3.org/2000/svg", "path");
  tip.setAttribute("d", "M2 1.5 L9 5 L2 8.5 Z");
  head.append(tip);
  // The SVG lives in a wrapper because Marker wants an HTMLElement to position.
  labels.push(
    new Marker({ element: h("div", null, head), anchor: "center" })
      .setLngLat([(off.lon + on.lon) / 2, (off.lat + on.lat) / 2])
      .addTo(map),
  );
}

function dayLabel(iso: string): string {
  const [, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTHS[(month ?? 1) - 1]}`;
}

/** A geodesic-ish circle as a polygon: fine at reach radii of a few hundred km. */
function circle(centre: Point, radiusKm: number): number[][] {
  const dLat = radiusKm / 111.2;
  const dLon = radiusKm / (111.2 * Math.cos((centre.lat * Math.PI) / 180));
  const ring: number[][] = [];
  for (let i = 0; i <= CIRCLE_POINTS; i += 1) {
    const a = (i / CIRCLE_POINTS) * 2 * Math.PI;
    ring.push([centre.lon + dLon * Math.cos(a), centre.lat + dLat * Math.sin(a)]);
  }
  return ring;
}

function point(role: "off" | "on" | "reported", at: Point): GeoJSON.Feature {
  return { type: "Feature", properties: { role }, geometry: { type: "Point", coordinates: [at.lon, at.lat] } };
}

/** Add the story source and its three layers. Call once the map has loaded. */
export function addStoryLayers(map: MapLibreMap): void {
  map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "stories-reach",
    type: "fill",
    source: SOURCE,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#C9D6E3", "fill-opacity": 0.04 },
  });
  map.addLayer({
    id: "stories-reach-edge",
    type: "line",
    source: SOURCE,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "line-color": "#C9D6E3", "line-opacity": 0.75, "line-width": 1 },
  });
  // Tracks: a listed ship's dark passage (state colour) and a faked cluster track (grey).
  map.addLayer({
    id: "stories-gap",
    type: "line",
    source: SOURCE,
    filter: ["==", ["geometry-type"], "LineString"],
    paint: {
      "line-color": ["match", ["get", "kind"], "false", "#8A98A6", "#F0A33A"],
      "line-width": 1.5,
      "line-dasharray": [2, 2],
    },
  });
  // Points: 'off' is a listed ship's departure; 'reported' is a cluster's departure, hollow
  // because the position was not real; 'on' is where the ship came back.
  map.addLayer({
    id: "stories-points",
    type: "circle",
    source: SOURCE,
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": 4.5,
      "circle-color": ["match", ["get", "role"], "off", "#F0A33A", "reported", "#0C1218", "#E6ECF2"],
      "circle-stroke-color": ["match", ["get", "role"], "reported", "#A9B6C3", "#0C1218"],
      "circle-stroke-width": ["match", ["get", "role"], "reported", 1.5, 1],
    },
  });
}

/** Risk tags that make a listing a story; a port-inspection record alone is not one (mirrors core RISK_TAGS). */
const RISK_TAGS = ["sanction", "mare.shadow", "poi", "reg.warn"];

/**
 * The card the strait view leads with: the shadow-fleet ship, else the first risk-tagged ship.
 * Null when no listed ship carries a risk tag, so an untagged port record never leads a card.
 */
export function leadListed(stories: HormuzStories): StoryListed | null {
  const tagged = (stories.listed ?? []).filter((s) => s.lists.some((tag) => RISK_TAGS.includes(tag)));
  return tagged.find((s) => s.lists.includes("mare.shadow")) ?? tagged[0] ?? null;
}

/** The cluster card: the first cluster. */
export function leadCluster(stories: HormuzStories): StoryCluster | null {
  return (stories.clusters ?? [])[0] ?? null;
}

/** Empty the story layers: the strait view with no card drawn. */
export function clearStories(map: MapLibreMap): void {
  clearLabels();
  map.getSource<GeoJSONSource>(SOURCE)?.setData({ type: "FeatureCollection", features: [] });
}

/**
 * Draw a card's geometry and frame it. The padding keeps track ends off the edges
 * (the 1,334 km cluster tracks need the whole box); the zoom cap stops a short
 * track from filling the screen.
 */
function show(map: MapLibreMap, features: GeoJSON.Feature[], bounds: [number, number][], maxZoom: number): void {
  // Every card clears the previous card's labels, so nothing stacks up across switches.
  clearLabels();
  const source = map.getSource<GeoJSONSource>(SOURCE);
  source?.setData({ type: "FeatureCollection", features });
  if (bounds.length > 0) {
    const lons = bounds.map((p) => p[0]);
    const lats = bounds.map((p) => p[1]);
    map.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 70, maxZoom, duration: 600 },
    );
  }
}

/** Card 1: the off and on points, the dashed gap between them, and the reach circle. */
export function showListed(map: MapLibreMap, story: StoryListed): void {
  const features: GeoJSON.Feature[] = [
    {
      type: "Feature",
      properties: { kind: "gap" },
      geometry: {
        type: "LineString",
        coordinates: [
          [story.off.lon, story.off.lat],
          [story.on.lon, story.on.lat],
        ],
      },
    },
    point("off", story.off),
    point("on", story.on),
    {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [circle(story.off, story.reach_km)] },
    },
  ];
  // Frame the reach circle, not just the two points, so the reach boundary is on screen.
  show(map, features, circle(story.off, story.reach_km) as [number, number][], 9);
  // Labels come after show(), which clears the previous card's labels.
  // story.start carries a time, so dayLabel gets the date half only.
  const when = `${dayLabel(story.start.slice(0, 10))} ${story.start.slice(11, 16)} UTC`;
  placeLabel(map, h("div", { class: "hz-map-label" }, `Switched off ${when}`), story.off, "top", 9);
  placeLabel(map, h("div", { class: "hz-map-label" }, "Came back"), story.on, "bottom", -9);
  // The circle's northern vertex: this radius formula is the same one circle() uses.
  const north = { lat: story.off.lat + story.reach_km / 111.2, lon: story.off.lon };
  const reachText = `Where it could have gone in ${story.hours.toFixed(1)} h`;
  placeLabel(map, h("div", { class: "hz-map-label" }, reachText), north, "bottom", -8);
  addArrow(map, story.off, story.on);
}

/** Card 2: every ship in the cluster, off and on, so the shared reappearance spot is visible. */
export function showCluster(map: MapLibreMap, cluster: StoryCluster): void {
  // Each member: a grey track from its reported departure (hollow) to the spot it came back to.
  const features: GeoJSON.Feature[] = cluster.points.flatMap((p) => [
    {
      type: "Feature",
      properties: { kind: "false" },
      geometry: {
        type: "LineString",
        coordinates: [
          [p.off.lon, p.off.lat],
          [p.on.lon, p.on.lat],
        ],
      },
    } as GeoJSON.Feature,
    point("reported", p.off),
    point("on", p.on),
  ]);
  show(
    map,
    features,
    cluster.points.flatMap((p) => [
      [p.off.lon, p.off.lat],
      [p.on.lon, p.on.lat],
    ]),
    7,
  );
}

/** The cluster card's text: every number from the cluster, the physics that rules out real voyages. */
function clusterSentence(c: StoryCluster): string {
  const head = `${dayLabel(c.day)}: ${c.vessels} ships switched off their transponders within ${c.span_hours.toFixed(1)} hours.`;
  const where = ` Their last positions were up to ${fmtInt(Math.round(c.max_km))} km out in the Arabian Sea. About ${Math.round(c.median_hours)} h later they came back at the same spot off the UAE.`;
  const speed =
    c.median_kn === null ? "" : ` The trip needs ${Math.round(c.median_kn)} knots, which these ships can't sail.`;
  return `${head}${where}${speed} Their positions look faked, likely by GPS interference, so we don't rank them.`;
}

/** Whole days from the strike day to the ship's switch-off (the card's "N days after the strikes"). */
export function daysAfterStrikes(startIso: string): number {
  return Math.floor((Date.parse(startIso) - STRIKE_DAY) / DAY_MS);
}

function storyCard(key: HormuzCardKey, text: string, onPick: (key: HormuzCardKey) => void): HTMLElement {
  // The card stays the one button (data-card, aria-pressed, is-active); "more" is its sibling,
  // because a button inside a button is neither valid nor clickable on its own.
  const body = h("span", { class: "hz-card-text" }, text);
  const card = h(
    "button",
    { class: "hz-card", type: "button", "data-card": key, "aria-pressed": "false" },
    body,
  );
  card.addEventListener("click", () => onPick(key));
  const more = h(
    "button",
    { class: "hz-more", type: "button", "aria-expanded": "false" },
    "more",
  );
  const wrap = h("div", { class: "hz-card-wrap" }, card, more);
  more.addEventListener("click", () => {
    const open = wrap.classList.toggle("is-open");
    more.setAttribute("aria-expanded", open ? "true" : "false");
    more.textContent = open ? "less" : "more";
  });
  return wrap;
}

/**
 * The two story cards under the headline: the lead listed ship and the first cluster.
 * Each wrapper holds the clickable card (which carries its key) and the "more" control;
 * the screen decides what a click means (open or close).
 */
export function storyCards(stories: HormuzStories, onPick: (key: HormuzCardKey) => void): HTMLElement[] {
  const cards: HTMLElement[] = [];
  const lead = leadListed(stories);
  if (lead !== null) {
    const name = lead.name ?? `MMSI ${lead.mmsi}`;
    const shadow = lead.lists.includes("mare.shadow") ? "a shadow-fleet ship (ships that move sanctioned oil), " : "";
    const days = daysAfterStrikes(lead.start);
    // Direction from the two positions; "closer to the strait" only when the data says so.
    const way = lead.on.lon < lead.off.lon ? "west" : "east";
    const closer = (lead.closer_to_strait_km ?? 0) > 0 ? ", closer to the strait" : "";
    const moved = lead.moved_km === null ? "" : ` It came back ${fmtInt(Math.round(lead.moved_km))} km ${way}${closer}.`;
    cards.push(
      storyCard(
        "sea1",
        `${name}, ${shadow}switched off its transponder in the Gulf of Oman for ${lead.hours.toFixed(1)} h, ${days} days after the strikes.${moved}`,
        onPick,
      ),
    );
  }
  const cluster = leadCluster(stories);
  if (cluster !== null) cards.push(storyCard("cluster", clusterSentence(cluster), onPick));
  return cards;
}
