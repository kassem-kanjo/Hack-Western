/**
 * The two Hormuz story cards (#219) and their map drawing. Every number is read from
 * /api/hormuz/stories; the only constant is the strike date the card measures days from.
 * The cards claim no cargo and no meetings.
 */

import type * as maplibregl from "maplibre-gl";

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
export function addStoryLayers(map: maplibregl.Map): void {
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
  // Tracks: a listed ship's dark passage (state colour) and a false cluster track (grey).
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

function show(map: maplibregl.Map, features: GeoJSON.Feature[], bounds: [number, number][]): void {
  const source = map.getSource<maplibregl.GeoJSONSource>(SOURCE);
  source?.setData({ type: "FeatureCollection", features });
  if (bounds.length > 0) {
    const lons = bounds.map((p) => p[0]);
    const lats = bounds.map((p) => p[1]);
    map.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 60, maxZoom: 9, duration: 600 },
    );
  }
}

/** Card 1: the off and on points, the dashed gap between them, and the reach circle. */
export function showListed(map: maplibregl.Map, story: StoryListed): void {
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
  show(map, features, circle(story.off, story.reach_km) as [number, number][]);
}

/** Card 2: every ship in the cluster, off and on, so the shared reappearance spot is visible. */
export function showCluster(map: maplibregl.Map, cluster: StoryCluster): void {
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
  );
}

/** The cluster card's text: every number from the cluster, the physics that rules out real voyages. */
function clusterSentence(c: StoryCluster): string {
  const head = `${dayLabel(c.day)}: ${c.vessels} ships stopped broadcasting within ${c.span_hours.toFixed(1)} hours.`;
  const where = ` Their last positions were up to ${Math.round(c.max_km)} km out in the Arabian Sea, and they came back about ${Math.round(c.median_hours)} h later at the same spot off the UAE.`;
  const speed =
    c.median_kn === null
      ? ""
      : ` Getting there needs ${Math.round(c.median_kn)} knots; these ships can't do that.`;
  return `${head}${where}${speed} Their positions were corrupted, likely GPS interference, so we don't rank them as hiding.`;
}

/** One card per listed ship and per cluster, under the headline. Clicking draws it on the map. */
export function storyCards(
  stories: HormuzStories,
  onListed: (story: StoryListed) => void,
  onCluster: (cluster: StoryCluster) => void,
): HTMLElement[] {
  const cards: HTMLElement[] = [];
  // The lead card is the listed ship on a shadow-fleet list, else the first listed ship.
  const lead = stories.listed.find((s) => s.lists.includes("mare.shadow")) ?? stories.listed[0];
  for (const story of lead ? [lead] : []) {
    const name = story.name ?? `MMSI ${story.mmsi}`;
    const shadow = story.lists.includes("mare.shadow") ? "on a shadow-fleet list, " : "";
    const days = Math.floor((Date.parse(story.start) - STRIKE_DAY) / DAY_MS);
    // Direction from the two positions; "closer to the strait" only when the data says so.
    const way = story.on.lon < story.off.lon ? "west" : "east";
    const closer = (story.closer_to_strait_km ?? 0) > 0 ? ", closer to the strait" : "";
    const moved =
      story.moved_km === null ? "" : ` It came back ${Math.round(story.moved_km)} km ${way}${closer}.`;
    const card = h(
      "button",
      { class: "hz-card", type: "button" },
      h(
        "span",
        { class: "hz-card-text" },
        `${name}, ${shadow}switched off its transponder in the Gulf of Oman for ${story.hours.toFixed(1)} h, ${days} days after the strikes.${moved}`,
      ),
    );
    card.addEventListener("click", () => onListed(story));
    cards.push(card);
  }
  for (const cluster of stories.clusters.slice(0, 1)) {
    const card = h(
      "button",
      { class: "hz-card", type: "button" },
      h(
        "span",
        { class: "hz-card-text" },
        clusterSentence(cluster),
      ),
    );
    card.addEventListener("click", () => onCluster(cluster));
    cards.push(card);
  }
  return cards;
}
