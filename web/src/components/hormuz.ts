/**
 * "The Strait of Hormuz: the fleet nobody declares" (#88/#89 UI).
 *
 * One rail block, mounted by the overview: a daily line of the radar-only share,
 * the plain sentence that reads it, two optional map layers and the caveats one
 * click down. It fetches its own two snapshots so the overview paint never waits
 * on them, and it hides its numbers behind nothing: chart and sentence first,
 * method last.
 */

import type * as maplibregl from "maplibre-gl";

import { h } from "./dom";
import { hormuzChart } from "./hormuzChart";
import { hormuzLayers, type HormuzLayersHandle } from "./hormuzLayers";
import { headlineShare, type HormuzHistory, type HormuzUnseen } from "./hormuzTypes";
import "./hormuz.css";

export interface HormuzBlockHandle {
  element: HTMLElement;
  destroy(): void;
}

const UNSEEN_URL = "/api/hormuz/unseen";
const HISTORY_URL = "/api/hormuz/history";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
  return (await response.json()) as T;
}

/** "2026-08-15" -> "15 Aug". */
function dayLabel(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  return `${String(date).padStart(2, "0")} ${MONTHS[month - 1] ?? ""}`.trim();
}

function periodLabel(
  period: { start: string; end: string } | undefined,
  days: number | undefined,
): string {
  if (!period) return days === undefined ? "" : `${days} days`;
  const start = dayLabel(period.start);
  const end = new Date(`${period.end}T00:00:00Z`);
  const endLabel = `${dayLabel(period.end)} ${end.getUTCFullYear()}`;
  return days === undefined ? `${start} \u2013 ${endLabel}` : `${start} \u2013 ${endLabel} \u00b7 ${days} days`;
}

/** "about 5 in 10", with honest wording at the two ends of the scale. */
function inTenPhrase(share: number): string {
  const count = Math.round(share * 10);
  if (count <= 0) return "fewer than 1 in 10";
  if (count >= 10) return "all";
  return `about ${count} in 10`;
}

interface ToggleOptions {
  layer: "radar" | "hotspots";
  label: string;
  enabled: boolean;
  act(on: boolean): void;
}

function layerToggle(opts: ToggleOptions): HTMLButtonElement {
  const button = h(
    "button",
    {
      class: "hormuz-toggle",
      type: "button",
      "data-layer": opts.layer,
      "aria-pressed": "false",
    },
    h("span", { class: "key", "aria-hidden": "true" }),
    opts.label,
  );

  if (!opts.enabled) {
    button.disabled = true;
    return button;
  }

  let on = false;
  button.addEventListener("click", () => {
    on = !on;
    button.setAttribute("aria-pressed", on ? "true" : "false");
    opts.act(on);
  });
  return button;
}

function caveats(unseen: HormuzUnseen, history: HormuzHistory): HTMLElement {
  const lines = [...new Set([...(unseen.caveats ?? []), ...(history.caveats ?? [])])];
  const sources: HTMLElement[] = [];
  if (unseen.source) {
    sources.push(h("p", { class: "source" }, "Radar: ", unseen.source));
  }
  if (history.source) {
    sources.push(h("p", { class: "source" }, "History: ", history.source));
  }

  return h(
    "details",
    { class: "hormuz-caveats" },
    h("summary", null, "Caveats and sources"),
    h("ul", null, ...lines.map((line) => h("li", null, line))),
    ...sources,
  );
}

function paint(
  body: HTMLElement,
  unseen: HormuzUnseen,
  history: HormuzHistory,
  layers: HormuzLayersHandle,
): void {
  const share = headlineShare(unseen.summary);
  const sentence =
    share === null
      ? "This snapshot does not carry a headline dark share."
      : `On a typical day ${inTenPhrase(share)} ships radar sees here are not broadcasting`;

  const period = periodLabel(unseen.period ?? history.period, unseen.summary?.days);

  const children: (Node | string)[] = [];
  if (period !== "") children.push(h("p", { class: "hormuz-period" }, period));
  children.push(
    h("p", { class: "hormuz-lede" }, sentence),
    hormuzChart(unseen.daily, unseen.summary),
    h(
      "div",
      { class: "hormuz-toggles", role: "group", "aria-label": "Hormuz map layers" },
      layerToggle({
        layer: "radar",
        label: "Radar, not broadcasting",
        enabled: unseen.grid.length > 0,
        act: (on) => layers.setRadar(on),
      }),
      layerToggle({
        layer: "hotspots",
        label: "Loitering hotspots",
        enabled: history.hotspots.length > 0,
        act: (on) => layers.setHotspots(on),
      }),
    ),
    h(
      "p",
      { class: "hormuz-key" },
      `${unseen.grid.length.toLocaleString("en-US")} radar-only cells \u00b7 ` +
        `${history.hotspots.length.toLocaleString("en-US")} loitering hotspots`,
    ),
    caveats(unseen, history),
  );

  body.replaceChildren(...children);
}

/** Build the block and start its two fetches. The caller appends `element`. */
export function hormuzBlock(map: maplibregl.Map): HormuzBlockHandle {
  const body = h("div", { class: "hormuz-body" }, h("p", { class: "hormuz-error" }, "Loading the strait..."));
  const element = h(
    "section",
    { class: "rail-section hormuz" },
    h("h3", null, "The Strait of Hormuz: the fleet nobody declares"),
    body,
  );

  const abort = new AbortController();
  let layers: HormuzLayersHandle | null = null;

  Promise.all([
    getJson<HormuzUnseen>(UNSEEN_URL, abort.signal),
    getJson<HormuzHistory>(HISTORY_URL, abort.signal),
  ])
    .then(([unseen, history]) => {
      if (abort.signal.aborted) return;
      layers = hormuzLayers(map, unseen.grid, history.hotspots);
      paint(body, unseen, history, layers);
    })
    .catch(() => {
      if (abort.signal.aborted) return;
      body.replaceChildren(
        h("p", { class: "hormuz-error" }, "The Hormuz snapshot is not loaded."),
      );
    });

  return {
    element,
    destroy(): void {
      abort.abort();
      layers?.destroy();
      layers = null;
    },
  };
}
