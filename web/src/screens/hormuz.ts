/**
 * Hormuz view (#200 V3, demo beat): the story, top to bottom.
 *
 *   1. The headline: how many ships here hide from tracking, since the war began,
 *      against the Gulf of Oman control. Figures from /api/hormuz/trend regime.
 *   2. One chart: pooled share not broadcasting, two years, Hormuz against control.
 *   3. Two story cards that draw a story on the map, and the data credit.
 *
 * Every number comes from the API.
 */

import * as maplibregl from "maplibre-gl";

import { offlineStyle } from "../basemap";
import { attribution } from "../components/Attribution";
import { h } from "../components/dom";
import { guideLine } from "../components/GuideLine";
import { fetchHormuz } from "../components/hormuz";
import {
  addStoryAnchors,
  addStoryLayers,
  clearStories,
  clearStoryAnchors,
  leadCluster,
  leadListed,
  showCluster,
  showListed,
  storyCards,
  type HormuzStories,
} from "../components/hormuzStories";
import { trendChart, trendHeadline, type HormuzTrend } from "../components/hormuzTrend";
import { themeColor } from "../map/theme";
import { topBar } from "../components/TopBar";
import {
  onHormuzCardRequest,
  pendingHormuzCard,
  requestHormuzCard,
  type HormuzCardKey,
} from "../present/cardRequest";
import "./hormuz.css";

export interface HormuzScreen {
  destroy(): void;
}

const TREND_URL = "/api/hormuz/trend";
const STORIES_URL = "/api/hormuz/stories";

/**
 * The two boxes the headline and the chart compare. The API sends them with the
 * trend, but an older API may not: everything here is optional and unvalidated
 * data until readAreas() has checked it, so a missing block draws nothing.
 */
type AreaRole = "treated" | "control";

interface AreaShape {
  id: string;
  /** The API's label; the default wording below when the API omits it. */
  label: string;
  role: AreaRole;
  geometry: AreaPolygon;
}

/** A closed GeoJSON Polygon, kept as lon/lat tuples so bounds maths stays typed. */
interface AreaPolygon {
  type: "Polygon";
  coordinates: [number, number][][];
}

interface HormuzAreas {
  treated: AreaShape;
  control: AreaShape;
}

/** Default labels for when the API sends none. */
const AREA_LABELS: Record<AreaRole, string> = {
  treated: "Hormuz (measured)",
  control: "Gulf of Oman (comparison)",
};

/**
 * Everything restylable about the two areas, in one place. Colours are read from
 * the stylesheet tokens at draw time (MapLibre cannot read CSS variables);
 * `--st-sanctioned` is the red that `--st-error` aliases. Line widths and fill
 * strengths live here too, so a restyle touches this object and no drawing code.
 */
const AREA_STYLE: Record<AreaRole, { token: string; fallback: string; fillOpacity: number; lineWidth: number }> = {
  treated: { token: "--st-sanctioned", fallback: "#F26457", fillOpacity: 0.09, lineWidth: 1.5 },
  control: { token: "--text-3", fallback: "#8A98A6", fillOpacity: 0.03, lineWidth: 1 },
};

const AREA_SOURCE = "hormuz-areas";
/** Area layers sit under the story layers (whose ids all start this way). */
const STORY_LAYER_PREFIX = "stories-";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** A GeoJSON Polygon with finite [lon, lat] points, or null when malformed. */
function readPolygon(value: unknown): AreaPolygon | null {
  if (!isRecord(value) || value.type !== "Polygon" || !Array.isArray(value.coordinates)) return null;
  const rings: [number, number][][] = [];
  for (const ring of value.coordinates) {
    if (!Array.isArray(ring) || ring.length < 4) return null;
    const points: [number, number][] = [];
    for (const point of ring) {
      if (!Array.isArray(point) || point.length < 2) return null;
      const [lon, lat] = point;
      if (typeof lon !== "number" || typeof lat !== "number") return null;
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
      points.push([lon, lat]);
    }
    rings.push(points);
  }
  return rings.length === 0 ? null : { type: "Polygon", coordinates: rings };
}

function readArea(value: unknown, role: AreaRole): AreaShape | null {
  if (!isRecord(value)) return null;
  const geometry = readPolygon(value.geometry);
  if (geometry === null) return null;
  const id = typeof value.id === "string" && value.id.trim() !== "" ? value.id : role;
  const label = typeof value.label === "string" && value.label.trim() !== "" ? value.label : AREA_LABELS[role];
  return { id, label, role, geometry };
}

/** Both areas or nothing: a half-drawn comparison would be a lie on the map. */
function readAreas(value: unknown): HormuzAreas | null {
  if (!isRecord(value)) return null;
  const treated = readArea(value.treated, "treated");
  const control = readArea(value.control, "control");
  return treated === null || control === null ? null : { treated, control };
}

/** Every vertex, for the first-view bounds. Rings are short (a box is five points). */
function areaPoints(area: AreaShape): [number, number][] {
  return area.geometry.coordinates.flatMap((ring) => ring);
}

/**
 * Where an area's name sits. The centroid, except the control label is lifted into
 * the upper part of its box: the plain "Gulf of Oman" place name now marks its centre.
 */
function areaLabelPoint(area: AreaShape): [number, number] {
  const points = areaPoints(area);
  const lons = points.map((point) => point[0]);
  const lats = points.map((point) => point[1]);
  const south = Math.min(...lats);
  const north = Math.max(...lats);
  const lon = (Math.min(...lons) + Math.max(...lons)) / 2;
  const lat = area.role === "control" ? south + (north - south) * 0.75 : (south + north) / 2;
  return [lon, lat];
}

/** The strait box itself; the first view is widened to its union with the story places. */
const STRAIT: [[number, number], [number, number]] = [
  [55.6, 25.6],
  [57.2, 27.1],
];

/**
 * Plain place names for the first view. The offline basemap has no text glyphs, so these are
 * DOM markers; they are not story labels and never move or clear.
 */
const PLACES: { name: string; lon: number; lat: number }[] = [
  { name: "Iran", lon: 56.0, lat: 27.7 },
  { name: "Oman", lon: 57.9, lat: 23.6 },
  { name: "United Arab Emirates", lon: 55.2, lat: 24.4 },
  { name: "Strait of Hormuz", lon: 56.3, lat: 26.6 },
  // The centre of the control box, so the plain name labels the grey area.
  { name: "Gulf of Oman", lon: 58.7, lat: 24.4 },
];

/**
 * The first view and "Back to strait" share this frame. The strait box alone cuts off both
 * story places, the comparison boxes and the outer place names, so the frame is their
 * union with padding room for the label pills.
 */
function firstViewBounds(
  stories: HormuzStories | null,
  areas: HormuzAreas | null,
): [[number, number], [number, number]] {
  const points: [number, number][] = [
    STRAIT[0],
    STRAIT[1],
    ...PLACES.map((place): [number, number] => [place.lon, place.lat]),
  ];
  if (areas !== null) points.push(...areaPoints(areas.treated), ...areaPoints(areas.control));
  if (stories !== null) {
    const listed = leadListed(stories);
    if (listed !== null) points.push([listed.off.lon, listed.off.lat]);
    const cluster = leadCluster(stories);
    if (cluster !== null) points.push([cluster.centre.lon, cluster.centre.lat]);
  }
  const lons = points.map((point) => point[0]);
  const lats = points.map((point) => point[1]);
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}

/**
 * Frame padding. The top is wider so the strait's place name clears the guidance banner and
 * the place pill; the other sides leave room for the outer place names and the attribution.
 */
const VIEW_PADDING = { top: 110, right: 56, bottom: 56, left: 56 };

function headlineBlock(trend: HormuzTrend): HTMLElement {
  const figures = trend.regime ? trendHeadline(trend.regime) : null;
  if (figures === null) {
    return h("div", { class: "hz-head" }, h("h1", { class: "hz-sentence" }, "The Hormuz trend is not in the snapshot yet."));
  }
  return h(
    "div",
    { class: "hz-head" },
    h("h1", { class: "hz-sentence" }, figures.sentence),
    h(
      "div",
      { class: "hz-figure" },
      h("span", { class: "hz-big" }, figures.big),
      h("span", { class: "hz-before" }, figures.before),
    ),
    h("p", { class: "hz-next" }, figures.control),
    h("p", { class: "hz-small" }, figures.difference),
  );
}

function chartBlock(trend: HormuzTrend): HTMLElement {
  return h(
    "section",
    { class: "hz-section" },
    h("h2", { class: "hz-label" }, "Share of radar-seen ships not broadcasting"),
    trendChart(trend),
  );
}

export function renderHormuz(app: HTMLElement): HormuzScreen {
  const abort = new AbortController();
  let destroyed = false;
  let stories: HormuzStories | null = null;
  /** The comparison boxes from the trend; null until (or unless) the API sends them. */
  let areas: HormuzAreas | null = null;
  /** Drawn once, whichever of the map and the trend arrives last. */
  let areasDrawn = false;
  /** The story card drawn on the map; null is the strait view. */
  let activeCard: HormuzCardKey | null = null;
  let homeBounds = firstViewBounds(null, null);
  let anchorsDrawn = false;
  const cardButtons: HTMLButtonElement[] = [];
  const placeMarkers: maplibregl.Marker[] = [];
  const areaMarkers: maplibregl.Marker[] = [];

  const mapDiv = h("div", { class: "map hormuz-map" });
  // Returns to the strait view; shown only while a card is open.
  const backButton = h(
    "button",
    { class: "hz-back", type: "button", hidden: true },
    "Back to strait",
  );
  backButton.addEventListener("click", () => requestHormuzCard(null));
  const stage = h(
    "section",
    { class: "stage stage-hormuz", "aria-label": "The Strait of Hormuz" },
    mapDiv,
    h("p", { class: "hormuz-place" }, "Strait of Hormuz", h("span", null, " · Gulf of Oman")),
    backButton,
    attribution(),
  );

  // Labels the cluster's hollow dots on the map (the offline basemap has no text glyphs).
  const mapKey = h(
    "p",
    { class: "hz-map-key", hidden: true },
    "○ reported position · ● reappeared here · grey dashes: faked track",
  );
  stage.append(mapKey);

  const body = h("div", { class: "hz-body" }, h("p", { class: "hz-small" }, "Loading the strait..."));
  const rail = h("aside", { class: "rail hormuz-rail", "aria-label": "The strait" }, body);

  app.replaceChildren(
    topBar({ active: "hormuz", mode: "SNAPSHOT", generatedAt: null, caseHref: null, demoHref: null }),
    guideLine(
      "hormuz",
      "Why it matters: since the war, most ships here stopped broadcasting. Click a story card to see one.",
    ),
    h("main", { class: "main hormuz-main" }, stage, rail),
  );

  const map = new maplibregl.Map({
    container: mapDiv,
    style: offlineStyle(),
    bounds: homeBounds,
    fitBoundsOptions: { padding: VIEW_PADDING },
    attributionControl: false,
    interactive: true,
  });

  // Place names sit on the map from the first paint, so the offline basemap never reads as blank.
  function addPlaceLabels(): void {
    for (const place of PLACES) {
      const element = h("div", { class: "hz-place-label" }, place.name);
      placeMarkers.push(
        new maplibregl.Marker({ element, anchor: "center" }).setLngLat([place.lon, place.lat]).addTo(map),
      );
    }
  }

  let mapReady = false;

  /**
   * Draw the two comparison boxes and their labels, once. Safe before the map or
   * the trend exists: it is called again when each of them arrives, and the guard
   * keeps later renders from adding a second copy.
   */
  function drawAreas(): void {
    if (destroyed || !mapReady || areasDrawn || areas === null) return;
    areasDrawn = true;
    const boxes = areas;
    // Insert under the story layers so a card's dots and tracks always read on top.
    const firstStory = map.getStyle().layers.find((layer) => layer.id.startsWith(STORY_LAYER_PREFIX))?.id;
    const features: GeoJSON.Feature[] = (["control", "treated"] as const).map((role) => ({
      type: "Feature",
      properties: { role },
      geometry: boxes[role].geometry,
    }));
    try {
      map.addSource(AREA_SOURCE, { type: "geojson", data: { type: "FeatureCollection", features } });
      // Control first, then treated, so the red edge stays visible where they meet.
      for (const role of ["control", "treated"] as const) {
        const style = AREA_STYLE[role];
        const colour = themeColor(style.token, style.fallback);
        map.addLayer(
          {
            id: `hormuz-area-${role}-fill`,
            type: "fill",
            source: AREA_SOURCE,
            filter: ["==", ["get", "role"], role],
            paint: { "fill-color": colour, "fill-opacity": style.fillOpacity },
          },
          firstStory,
        );
        map.addLayer(
          {
            id: `hormuz-area-${role}-line`,
            type: "line",
            source: AREA_SOURCE,
            filter: ["==", ["get", "role"], role],
            paint: { "line-color": colour, "line-width": style.lineWidth },
          },
          firstStory,
        );
      }
    } catch {
      // A style that cannot take the layers must not take the screen down with it.
    }
    for (const area of [boxes.treated, boxes.control]) {
      const [lon, lat] = areaLabelPoint(area);
      const element = h("div", { class: `hz-area-label is-${area.role}` }, area.label);
      areaMarkers.push(
        new maplibregl.Marker({ element, anchor: "center" }).setLngLat([lon, lat]).addTo(map),
      );
    }
  }

  map.on("load", () => {
    if (destroyed) return;
    addStoryLayers(map);
    addPlaceLabels();
    mapReady = true;
    drawAreas();
    drawCard();
  });

  /**
   * Draw the active card, or the strait view. Safe before the map or the stories
   * exist: it runs again when each of them arrives.
   */
  function drawCard(): void {
    const on = (button: HTMLButtonElement): void => {
      const key = button.dataset["card"] ?? "";
      const pressed = key === activeCard;
      button.closest(".hz-card")?.classList.toggle("is-active", pressed);
      button.setAttribute("aria-pressed", pressed ? "true" : "false");
    };
    cardButtons.forEach(on);
    backButton.hidden = activeCard === null;
    mapKey.hidden = activeCard !== "cluster";
    if (!mapReady) return;
    // The two first-view markers show only in the strait view. While a card is open, only that
    // card's labels are on the map, so the markers never collide with them.
    if (stories !== null) {
      if (activeCard === null && !anchorsDrawn) {
        addStoryAnchors(map, stories, (key) => requestHormuzCard(activeCard === key ? null : key));
        anchorsDrawn = true;
      } else if (activeCard !== null && anchorsDrawn) {
        clearStoryAnchors();
        anchorsDrawn = false;
      }
    }
    if (activeCard === null || stories === null) {
      if (mapReady) clearStories(map);
      if (activeCard === null) map.fitBounds(homeBounds, { padding: VIEW_PADDING, duration: 600 });
      return;
    }
    if (activeCard === "sea1") {
      const story = leadListed(stories);
      if (story !== null) showListed(map, story);
    } else {
      const cluster = leadCluster(stories);
      if (cluster !== null) showCluster(map, cluster);
    }
  }

  /** A card request from anywhere (a click here, or the presentation). Same card again closes it. */
  function applyCard(card: HormuzCardKey | null): void {
    activeCard = card;
    drawCard();
  }

  /** Two cards under the headline; a click on the open card closes it. */
  function storyBlock(data: HormuzStories): HTMLElement {
    const cards = storyCards(data, (key) => requestHormuzCard(activeCard === key ? null : key));
    for (const card of cards) {
      // The button is the card itself; its wrapper also holds the separate "more" control.
      const button = card.querySelector<HTMLButtonElement>(".hz-card-open");
      if (button !== null) cardButtons.push(button);
    }
    return h(
      "section",
      { class: "hz-section hz-stories" },
      ...cards,
      h(
        "p",
        { class: "hz-small" },
        "Powered by ",
        h(
          "a",
          { href: "https://globalfishingwatch.org", target: "_blank", rel: "noopener noreferrer" },
          "Global Fishing Watch",
        ),
      ),
    );
  }

  // Requests come from clicks and from the presentation; the last one wins.
  const unsubscribe = onHormuzCardRequest(applyCard);
  const onEscape = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && activeCard !== null) requestHormuzCard(null);
  };
  window.addEventListener("keydown", onEscape);

  Promise.all([
    fetchHormuz<HormuzTrend & { areas?: unknown }>(TREND_URL, abort.signal),
    fetchHormuz<HormuzStories>(STORIES_URL, abort.signal).catch(() => null),
  ])
    .then(([trend, data]) => {
      if (destroyed || abort.signal.aborted) return;
      stories = data;
      areas = readAreas(trend.areas);
      homeBounds = firstViewBounds(data, areas);
      // Hero, chart, then the cards: tightened so the whole rail fits a 1280x720 window.
      const parts: HTMLElement[] = [headlineBlock(trend), chartBlock(trend)];
      if (data) parts.push(storyBlock(data));
      body.replaceChildren(...parts);
      drawAreas();
      drawCard();
    })
    .catch(() => {
      if (destroyed || abort.signal.aborted) return;
      body.replaceChildren(
        h(
          "p",
          { class: "hz-small" },
          "The Hormuz trend is not loaded. Start the API with DARKFLEET_DATA=data/snapshot uv run uvicorn darkfleet.api.app:app --port 8000.",
        ),
      );
    });

  // A card is per visit: leaving the screen closes it for the next one.
  applyCard(pendingHormuzCard());

  return {
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      unsubscribe();
      window.removeEventListener("keydown", onEscape);
      requestHormuzCard(null);
      abort.abort();
      clearStoryAnchors();
      for (const marker of placeMarkers) marker.remove();
      for (const marker of areaMarkers) marker.remove();
      map.remove();
    },
  };
}
