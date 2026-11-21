/**
 * Hormuz focus view (#117): the story opens here.
 *
 * Full screen, one question: how much of the fleet in the strait is invisible to
 * AIS? The map answers where, the rail answers how much: one hero number, the
 * 84-day daily series with its low-coverage days drawn faint, one comparison with
 * the public record, and the caveats one click down.
 *
 * The two layers start ON, because on this screen they are the argument rather
 * than an extra: violet cells are radar contacts that matched no broadcast, white
 * rings are where ships loiter. Attribution stays on the map; every number comes
 * from the frozen snapshot, never from local maths.
 */

import * as maplibregl from "maplibre-gl";

import { offlineStyle } from "../basemap";
import { attribution } from "../components/Attribution";
import { h } from "../components/dom";
import {
  HORMUZ_HISTORY_URL,
  HORMUZ_UNSEEN_URL,
  fetchHormuz,
  hormuzCaveats,
  periodLabel,
} from "../components/hormuz";
import { hormuzChart } from "../components/hormuzChart";
import { hormuzLayers, type HormuzLayersHandle } from "../components/hormuzLayers";
import {
  excludedDays,
  headlinePercent,
  type HormuzHistory,
  type HormuzUnseen,
} from "../components/hormuzTypes";
import { topBar } from "../components/TopBar";
import "./hormuz.css";

export interface HormuzScreen {
  destroy(): void;
}

const TITLE = "The Strait of Hormuz: the fleet nobody declares";

/** The one sentence under the hero number. */
const LEDE =
  "of vessels radar sees on a typical day have no matching location broadcast. Some are small boats that don't have to broadcast; many are not.";

/** The comparison with the best public source. Static by decision (demo beat 2). */
const GFW_SENTENCE =
  "Global Fishing Watch logged 1 'dark' gap event inside the strait from May to November (separate query): its rule only counts ships far offshore.";

/** The strait and the Gulf of Oman: the frame this screen always opens on. */
const STRAIT: [[number, number], [number, number]] = [
  [54, 22],
  [60.5, 27.5],
];

/** The window and how many of its days carried enough radar to count. */
function chartCaption(unseen: HormuzUnseen, history: HormuzHistory): string {
  const days = unseen.summary?.days ?? unseen.daily.length;
  const left = excludedDays(unseen.summary).length;
  const used = unseen.summary?.n_days_used ?? Math.max(0, days - left);
  const window = periodLabel(unseen.period ?? history.period, undefined);
  const coverage = days > 0 && used > 0 ? `${used} of ${days} days well covered` : "";
  return [window, coverage].filter((part) => part !== "").join(" \u00b7 ");
}

/** The two layers are already on; this is their key, not a control. */
function layerKey(): HTMLElement {
  return h(
    "div",
    { class: "hormuz-keys", role: "group", "aria-label": "Map layers shown" },
    h(
      "span",
      { class: "hormuz-key-item" },
      h("span", { class: "key is-radar", "aria-hidden": "true" }),
      "Radar, no matching broadcast",
    ),
    h(
      "span",
      { class: "hormuz-key-item" },
      h("span", { class: "key is-hotspot", "aria-hidden": "true" }),
      "Loitering hotspots",
    ),
  );
}

function hero(unseen: HormuzUnseen): HTMLElement {
  const percent = headlinePercent(unseen.summary);
  const number = percent === null ? "\u2014" : `${percent}%`;
  return h(
    "div",
    { class: "hormuz-hero" },
    h("p", { class: "hormuz-hero-num" }, number),
    h("p", { class: "hormuz-hero-lede" }, LEDE),
  );
}

function chartBlock(unseen: HormuzUnseen, history: HormuzHistory): HTMLElement {
  const excluded = excludedDays(unseen.summary);
  const children: (Node | string)[] = [
    h("p", { class: "hormuz-chart-caption" }, chartCaption(unseen, history)),
    hormuzChart(unseen.daily, unseen.summary, { excludedDays: excluded, height: 170 }),
  ];

  if (excluded.length > 0) {
    children.push(
      h(
        "p",
        { class: "hormuz-chart-key" },
        h("span", { class: "chart-key is-counted", "aria-hidden": "true" }),
        "counted day",
        h("span", { class: "chart-key is-excluded", "aria-hidden": "true" }),
        "low-coverage day, left out of the headline",
      ),
    );
  }

  return h(
    "section",
    { class: "hormuz-chart-block", "aria-label": "Daily dark share" },
    ...children,
    layerKey(),
  );
}

function gfwNote(): HTMLElement {
  return h(
    "section",
    { class: "hormuz-gfw" },
    h("p", { class: "hormuz-gfw-label" }, "On the public record"),
    h("p", { class: "hormuz-gfw-line" }, GFW_SENTENCE),
  );
}

function focusBody(unseen: HormuzUnseen, history: HormuzHistory): HTMLElement[] {
  return [hero(unseen), chartBlock(unseen, history), gfwNote(), hormuzCaveats(unseen, history)];
}

/** Paint the focus view into `app`; the router owns the returned handle. */
export function renderHormuz(app: HTMLElement): HormuzScreen {
  const abort = new AbortController();
  let destroyed = false;
  let layers: HormuzLayersHandle | null = null;

  const mapDiv = h("div", { class: "map hormuz-map" });
  const stage = h(
    "section",
    { class: "stage stage-hormuz", "aria-label": "The Strait of Hormuz" },
    mapDiv,
    h(
      "p",
      { class: "hormuz-place" },
      "Strait of Hormuz",
      h("span", null, " \u00b7 Gulf of Oman"),
    ),
    attribution(),
  );

  const body = h(
    "div",
    { class: "hormuz-focus-body" },
    h("p", { class: "hormuz-error" }, "Loading the strait..."),
  );
  const rail = h(
    "aside",
    { class: "rail hormuz-rail", "aria-label": "The strait in numbers" },
    h(
      "header",
      { class: "hormuz-head" },
      h("p", { class: "hormuz-eyebrow" }, "Global \u00b7 satellite radar"),
      h("h1", { class: "hormuz-title" }, TITLE),
    ),
    body,
  );

  app.replaceChildren(
    topBar({
      active: "hormuz",
      mode: "SNAPSHOT",
      generatedAt: null,
      caseHref: null,
      demoHref: null,
    }),
    h("main", { class: "main hormuz-main" }, stage, rail),
  );

  // The container is attached and sized before MapLibre measures it. `bounds`
  // frames the strait at construction, so the map never paints the world first.
  const map = new maplibregl.Map({
    container: mapDiv,
    style: offlineStyle(),
    bounds: STRAIT,
    fitBoundsOptions: { padding: 32 },
    attributionControl: false,
    interactive: true,
  });

  Promise.all([
    fetchHormuz<HormuzUnseen>(HORMUZ_UNSEEN_URL, abort.signal),
    fetchHormuz<HormuzHistory>(HORMUZ_HISTORY_URL, abort.signal),
  ])
    .then(([unseen, history]) => {
      if (destroyed || abort.signal.aborted) return;

      layers = hormuzLayers(map, unseen.grid, history.hotspots, { zoomOnEnable: false });
      layers.setRadar(true);
      layers.setHotspots(true);

      body.replaceChildren(...focusBody(unseen, history));
    })
    .catch(() => {
      if (destroyed || abort.signal.aborted) return;
      body.replaceChildren(
        h(
          "p",
          { class: "hormuz-error" },
          "The Hormuz snapshot is not loaded. Start the API with DARKFLEET_DATA=data/snapshot " +
            "uv run uvicorn darkfleet.api.app:app --port 8000.",
        ),
      );
    });

  return {
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      abort.abort();
      layers?.destroy();
      layers = null;
      map.remove();
    },
  };
}
