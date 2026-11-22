/**
 * Hormuz view (#200 V3, demo beat): three things, top to bottom.
 *
 *   1. The headline: how many ships here hide from tracking, since the war began,
 *      against the Gulf of Oman control. Figures from /api/hormuz/trend regime.
 *   2. One chart: weekly share not broadcasting, two years, Hormuz against control.
 *   3. Today's satellite pass: the radar detections on the map and as chips.
 *
 * Every number comes from the API; the map shows the detections as dots only.
 */

import * as maplibregl from "maplibre-gl";

import { offlineStyle } from "../basemap";
import { attribution } from "../components/Attribution";
import { h } from "../components/dom";
import { fetchHormuz } from "../components/hormuz";
import {
  addStoryLayers,
  clearStories,
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
const SAR_URL = "/api/sar/scan";
const STORIES_URL = "/api/hormuz/stories";
const DETECTIONS = "sar-detections";
const DOTS = "sar-dots";

/** The frame the screen always opens on: the strait, zoomed in. */
const STRAIT: [[number, number], [number, number]] = [
  [55.6, 25.6],
  [57.2, 27.1],
];

/** Said plainly under the chips: the receivers were off during this pass. */
const AIS_NOTE = "Our AIS receivers did not cover this pass, so we can't say which of these switched off.";

interface SarDetection {
  lat: number;
  lon: number;
  chip?: string;
}

interface SarScan {
  time?: string;
  counts?: { detected?: number };
  detections?: SarDetection[];
}

/** Chip files are named without their extension; the API serves them by that name. */
const chipUrl = (file: string): string => `/api/sar/chip/${file.replace(/\.png$/, "")}.png`;

/** "21 Nov 02:06 UTC" from the scan's ISO time. */
function passLabel(iso: string | undefined): string {
  const t = iso ? new Date(iso) : null;
  if (t === null || Number.isNaN(t.getTime())) return "";
  const day = t.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const hm = t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC", hour12: false });
  return `${day} ${hm} UTC`;
}

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
    h("p", { class: "hz-small" }, figures.control, " ", figures.difference),
  );
}

function chartBlock(trend: HormuzTrend): HTMLElement {
  return h(
    "section",
    { class: "hz-section" },
    h("h2", { class: "hz-label" }, "Share not broadcasting, 4-week average (weekly faint)"),
    trendChart(trend),
    h(
      "p",
      { class: "hz-legend" },
      h("span", { class: "hz-key is-treated" }, "Strait of Hormuz"),
      h("span", { class: "hz-key is-control" }, "Gulf of Oman (control)"),
    ),
  );
}

export function renderHormuz(app: HTMLElement): HormuzScreen {
  const abort = new AbortController();
  let destroyed = false;
  let sar: SarScan | null = null;
  let selected = 0;
  let stories: HormuzStories | null = null;
  /** The story card drawn on the map; null is the strait view. */
  let activeCard: HormuzCardKey | null = null;
  const cardButtons: HTMLButtonElement[] = [];

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
    "○ reported position · ● came back here · grey dashes: faked track",
  );
  stage.append(mapKey);

  const body = h("div", { class: "hz-body" }, h("p", { class: "hz-small" }, "Loading the strait..."));
  const rail = h("aside", { class: "rail hormuz-rail", "aria-label": "The strait" }, body);

  app.replaceChildren(
    topBar({ active: "hormuz", mode: "SNAPSHOT", generatedAt: null, caseHref: null, demoHref: null }),
    h("main", { class: "main hormuz-main" }, stage, rail),
  );

  const map = new maplibregl.Map({
    container: mapDiv,
    style: offlineStyle(),
    bounds: STRAIT,
    fitBoundsOptions: { padding: 32 },
    attributionControl: false,
    interactive: true,
  });

  const detections = (): GeoJSON.FeatureCollection<GeoJSON.Point, { i: number }> => ({
    type: "FeatureCollection",
    features: (sar?.detections ?? []).map((d, i) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [d.lon, d.lat] },
      properties: { i },
    })),
  });

  let mapReady = false;
  map.on("load", () => {
    if (destroyed) return;
    addStoryLayers(map);
    map.addSource(DETECTIONS, { type: "geojson", data: detections() });
    map.addLayer({
      id: DOTS,
      type: "circle",
      source: DETECTIONS,
      paint: {
        "circle-radius": ["case", ["==", ["get", "i"], selected], 6, 4],
        "circle-color": "#56B3EA",
        "circle-stroke-color": "#0C1218",
        "circle-stroke-width": 1,
      },
    });
    map.on("click", DOTS, (event) => {
      const i = Number(event.features?.[0]?.properties?.["i"]);
      if (Number.isFinite(i)) select(i);
    });
    map.on("mouseenter", DOTS, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", DOTS, () => (map.getCanvas().style.cursor = ""));
    mapReady = true;
    drawCard();
  });

  /** Show one detection: bigger chip, its older optical context, and the dot highlighted. */
  function select(index: number): void {
    selected = index;
    if (mapReady) {
      map.setPaintProperty(DOTS, "circle-radius", ["case", ["==", ["get", "i"], index], 6, 4]);
    }
    renderSarDetail();
    for (const chip of document.querySelectorAll<HTMLButtonElement>(".hz-chip")) {
      chip.setAttribute("aria-pressed", chip.dataset.index === String(index) ? "true" : "false");
    }
  }

  const sarBox = h("div", { class: "hz-sar-detail" });

  function renderSarDetail(): void {
    const d = sar?.detections?.[selected];
    if (!d || !d.chip) {
      sarBox.replaceChildren();
      return;
    }
    sarBox.replaceChildren(
      h(
        "figure",
        { class: "hz-radar" },
        h("img", { src: chipUrl(d.chip), alt: `Radar detection ${selected + 1}` }),
        h(
          "figcaption",
          { class: "hz-small" },
          `Radar detection ${selected + 1} of ${sar?.detections?.length ?? 0} · ${passLabel(sar?.time)}`,
        ),
      ),
    );
  }

  function satellitePass(scan: SarScan): HTMLElement {
    const count = scan.counts?.detected ?? scan.detections?.length ?? 0;
    const chips = (scan.detections ?? []).map((d, i) => {
      const button = h(
        "button",
        { class: "hz-chip", type: "button", "data-index": String(i), "aria-pressed": i === selected ? "true" : "false" },
        h("img", { src: d.chip ? chipUrl(d.chip) : "", alt: `Radar detection ${i + 1}`, loading: "lazy" }),
      );
      button.addEventListener("click", () => select(i));
      return button;
    });
    return h(
      "section",
      { class: "hz-section" },
      h("h2", { class: "hz-label" }, "Today's satellite pass"),
      h("p", { class: "hz-figure-line" }, `${count} ships seen by radar · ${passLabel(scan.time)}`),
      h("div", { class: "hz-chips" }, ...chips),
      sarBox,
      h("p", { class: "hz-small" }, AIS_NOTE),
    );
  }

  /**
   * Draw the active card, or the strait view. Safe before the map or the stories
   * exist: it runs again when each of them arrives.
   */
  function drawCard(): void {
    const on = (button: HTMLButtonElement): void => {
      const key = button.dataset["card"] ?? "";
      const pressed = key === activeCard;
      button.classList.toggle("is-active", pressed);
      button.setAttribute("aria-pressed", pressed ? "true" : "false");
    };
    cardButtons.forEach(on);
    backButton.hidden = activeCard === null;
    mapKey.hidden = activeCard !== "cluster";
    if (!mapReady) return;
    if (activeCard === null || stories === null) {
      if (mapReady) clearStories(map);
      if (activeCard === null) map.fitBounds(STRAIT, { padding: 32, duration: 600 });
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
      if (card instanceof HTMLButtonElement) cardButtons.push(card);
    }
    return h(
      "section",
      { class: "hz-section hz-stories" },
      ...cards,
      h("p", { class: "hz-small" }, data.source ?? "Powered by Global Fishing Watch"),
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
    fetchHormuz<SarScan>(SAR_URL, abort.signal).catch(() => null),
    fetchHormuz<HormuzStories>(STORIES_URL, abort.signal).catch(() => null),
  ])
    .then(([trend, scan, data]) => {
      if (destroyed || abort.signal.aborted) return;
      sar = scan;
      stories = data;
      // The radar pass sits under the headline and the cards, so its chips show at 1280x720.
      const parts: HTMLElement[] = [headlineBlock(trend)];
      if (data) parts.push(storyBlock(data));
      if (scan) parts.push(satellitePass(scan));
      parts.push(chartBlock(trend));
      body.replaceChildren(...parts);
      renderSarDetail();
      drawCard();
      if (mapReady) map.getSource<maplibregl.GeoJSONSource>(DETECTIONS)?.setData(detections());
      else map.once("load", () => map.getSource<maplibregl.GeoJSONSource>(DETECTIONS)?.setData(detections()));
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
      map.remove();
    },
  };
}
