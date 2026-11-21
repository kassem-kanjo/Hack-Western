import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";

import "./styles/tokens.css";
import "./styles/console.css";
import "./styles/screens.css";

import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { CasesResponse } from "./api";
import { fetchCases } from "./api";
import { h } from "./components/dom";
import { topBar } from "./components/TopBar";
import { renderCaseFile } from "./screens/caseFile";
import { rankCases, renderOverview } from "./screens/overview";

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
  renderToken += 1;
  const token = renderToken;

  const previous = current;
  current = null;
  previous?.destroy();

  if (cases === null) {
    if (loadError !== null) showError(loadError);
    else showLoading();
    return;
  }

  const caseId = caseIdFromHash();
  if (caseId === null) {
    current = renderOverview(app, cases);
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

window.addEventListener("hashchange", route);

route(); // first paint: the loading line until the snapshot arrives
loadCases();
