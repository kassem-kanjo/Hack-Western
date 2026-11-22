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
  { name: "Gulf of Oman", lon: 56.9, lat: 25.7 },
];

/**
 * The first view and "Back to strait" share this frame. The strait box alone cuts off both
 * story places and the outer place names, so the frame is their union with padding room
 * for the label pills.
 */
function firstViewBounds(stories: HormuzStories | null): [[number, number], [number, number]] {
  const points: [number, number][] = [
    STRAIT[0],
    STRAIT[1],
    ...PLACES.map((place): [number, number] => [place.lon, place.lat]),
  ];
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
  /** The story card drawn on the map; null is the strait view. */
  let activeCard: HormuzCardKey | null = null;
  let homeBounds = firstViewBounds(null);
  let anchorsDrawn = false;
  const cardButtons: HTMLButtonElement[] = [];
  const placeMarkers: maplibregl.Marker[] = [];

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
  map.on("load", () => {
    if (destroyed) return;
    addStoryLayers(map);
    addPlaceLabels();
    mapReady = true;
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
    fetchHormuz<HormuzTrend>(TREND_URL, abort.signal),
    fetchHormuz<HormuzStories>(STORIES_URL, abort.signal).catch(() => null),
  ])
    .then(([trend, data]) => {
      if (destroyed || abort.signal.aborted) return;
      stories = data;
      homeBounds = firstViewBounds(data);
      // Hero, chart, then the cards: tightened so the whole rail fits a 1280x720 window.
      const parts: HTMLElement[] = [headlineBlock(trend), chartBlock(trend)];
      if (data) parts.push(storyBlock(data));
      body.replaceChildren(...parts);
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
      map.remove();
    },
  };
}
