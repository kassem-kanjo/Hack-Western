import "@fontsource/ibm-plex-sans/300.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";

import "./styles/tokens.css";
import "./styles/console.css";
import "./styles/screens.css";
import "./styles/nav.css";
import "./styles/live.css";
import "./styles/event.css";
import "./styles/patrol.css";

import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { CasesResponse } from "./api";
import { fetchCases } from "./api";
import { h } from "./components/dom";
import { initIntro } from "./components/Intro";
// The Watch V3 status bar (screens/watch.ts) replaced the separate StatusBar component (#184).
import { mountToasts } from "./components/Toasts"; // #184
import { topBar } from "./components/TopBar";
import { renderCaseFile } from "./screens/caseFile";
import { renderHormuz } from "./screens/hormuz";
import { rankCases } from "./screens/overview";
import { renderEvidence } from "./screens/evidence";
import { parseLiveRoute, renderLive, type LiveRoute, type LiveScreen } from "./screens/live";
import { renderPatrol } from "./screens/patrol";
import { renderStory } from "./story/story";
import { parseWatchRoute, renderWatch, type WatchScreen } from "./screens/watch";
import { initPresent } from "./present/present";

// Phone overrides go last on purpose: they must outrank the stylesheets the
// screen modules import (overview.css, hormuz.css) at equal specificity.
import "./styles/phone.css";

// MapLibre's worker is a sibling file in the package: point it at the copy Vite
// emits so it is served locally (offline, no CDN).
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
maplibregl.setWorkerUrl(workerUrl);

/** Shown verbatim when the cases list cannot be fetched: the fix is a command. */
const API_DOWN =
  "The console could not reach the API. Start it with DARKFLEET_DATA=data/snapshot uv run uvicorn darkfleet.api.app:app --port 8000.";

interface Screen {
  destroy(): void;
}

// index.html still ships the pre-router "#map" placeholder. It is absolutely
// positioned over the viewport and would eat pointer events, so drop it before
// any screen creates its own map container.
document.getElementById("map")?.remove();

// The router owns one full-height grid. `index.html` has no #app today, but
// reusing an existing node keeps the boot idempotent if it ever gains one.
const existingApp = document.getElementById("app");
const app = existingApp ?? h("div", { id: "app", class: "app" });
app.classList.add("app");
if (existingApp === null) document.body.append(app);

let cases: CasesResponse | null = null;
let loadError: string | null = null;
let casesRequest: Promise<CasesResponse> | null = null;

/** The mounted screen, so a hash change can tear its maps and timers down. */
let current: Screen | null = null;

/** The mounted live screen and its region. Event-only hash changes call select() on it, no re-render. */
let currentLive: { region: string; screen: LiveScreen } | null = null;

/** The mounted watch screen. Alert-only hash changes call select() on it, keeping the map. */
let currentWatch: WatchScreen | null = null;

// Bumped on every navigation. A case-detail fetch that resolves after the user
// has moved on must not replace the screen they are looking at now.
let renderToken = 0;

function showLoading(): void {
  app.replaceChildren(h("div", { class: "empty" }, "Loading the snapshot..."));
}

function showError(message: string): void {
  app.replaceChildren(
    topBar({
      active: "overview",
      mode: "ERROR",
      generatedAt: null,
      caseHref: null,
      demoHref: null,
    }),
    h(
      "main",
      { class: "main" },
      h("section", { class: "stage" }, h("div", { class: "banner is-error" }, message)),
    ),
  );
}

/**
 * "#/live/<REGION>" or "#/live/<REGION>/event/<index>" -> region and event index (null when no
 * event is selected). Any other hash returns null. The region must be the snapshot alphabet.
 */
function liveFromHash(): LiveRoute | null {
  // One parser for every live route (event, kind, vessel): the screen and the router must agree.
  return parseLiveRoute(location.hash);
}

/** "#/case/<id>" -> id; "#/", "", or anything unrecognised -> overview. */
function caseIdFromHash(): string | null {
  const path = location.hash.startsWith("#") ? location.hash.slice(1) : location.hash;
  if (!path.startsWith("/case/")) return null;

  const encoded = path.slice("/case/".length);
  if (encoded === "") return null;

  try {
    return decodeURIComponent(encoded);
  } catch {
    // A malformed escape sequence is not a case id; the overview is the fallback.
    return null;
  }
}

function route(): void {
  // Watch, live and patrol do not need the cases snapshot, so they route before
  // the loading gate. "#/" is Watch now: the world view of every ship.
  const watch = parseWatchRoute(location.hash);
  if (watch !== null && currentWatch !== null) {
    // Same screen, only the selected alert changed: keep the map and the camera.
    currentWatch.select(watch.alertId, watch.areaId ?? null);
    return;
  }

  const live = liveFromHash();
  if (live !== null && currentLive !== null && currentLive.region === live.region) {
    // Same region, only the selected event changed: keep the map and the list alive.
    currentLive.screen.select(live.eventIndex);
    return;
  }

  renderToken += 1;
  const token = renderToken;

  const previous = current;
  currentLive?.screen.destroy();
  currentLive = null;
  currentWatch?.destroy();
  currentWatch = null;
  current = null;
  previous?.destroy();

  if (watch !== null) {
    currentWatch = renderWatch(app, watch);
    return;
  }

  if (live !== null) {
    currentLive = { region: live.region, screen: renderLive(app, live) };
    return;
  }

  if (location.hash === "#/patrol") {
    renderPatrol(app);
    return;
  }

  if (location.hash === "#/hormuz") { current = renderHormuz(app); return; }

  if (location.hash === "#/story") { current = renderStory(app); return; }

  if (cases === null) {
    if (loadError !== null) showError(loadError);
    else showLoading();
    return;
  }

  const caseId = caseIdFromHash();
  if (caseId === null) {
    current = renderEvidence(app, cases);
    return;
  }

  // One ranked list for the session: the router sorts once and both screens
  // read the same order.
  const ranked = rankCases(cases.cases);

  void renderCaseFile(app, caseId, ranked, cases.generated_at)
    .then((screen) => {
      if (token === renderToken) current = screen;
      else screen.destroy();
    })
    .catch(() => {
      // renderCaseFile renders its own failure state, so a rejection here would
      // be a programming error rather than a data problem; keep the console up.
    });
}

function loadCases(): void {
  if (casesRequest !== null) return;

  casesRequest = fetchCases({ limit: 200, status: "all" });
  casesRequest
    .then((response) => {
      cases = response;
      // Draw whatever the current hash asks for now that data exists.
      route();
    })
    .catch(() => {
      loadError = API_DOWN;
      route();
    });
}

// ---- #184: app-wide status bar + live alert toasts (components are new files) ----

mountToasts();

window.addEventListener("hashchange", route);

route(); // first paint: the loading line until the snapshot arrives
loadCases();

// Before initPresent(): on a first visit the intro card is modal, so its
// capture handler must see "P" (and Escape) before presentation mode does.
initIntro();
initPresent();
