/**
 * Watch screen, Hormuz radar view: the right-hand panel and the data it draws.
 *
 * The numbers are the two frozen snapshot files (`/api/hormuz/unseen` and
 * `/api/hormuz/history`), read as they are: nothing is estimated here. The map
 * only has an aggregated grid, so the time-lapse scales the whole layer by the
 * day's count; it never invents where a vessel was on a given day.
 */

import type { GfwEncounter, HormuzHistoryFile, HormuzUnseenFile } from "../api";
import { h } from "../components/dom";
import type { RadarMapView } from "../map/watchMap";

const SVG_NS = "http://www.w3.org/2000/svg";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** 84 days in about six seconds. */
const PLAY_STEP_MS = 70;
const HOTSPOTS_SHOWN = 5;

export const GFW_URL = "https://globalfishingwatch.org";

/** "2026-11-14" -> "14 Nov". */
export function fmtDay(day: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  if (match === null) return day;
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1] ?? ""}`;
}

function pct(share: number): string {
  return `${Math.round(share * 100)}%`;
}

function partyName(name: string | null | undefined, mmsi: string | null | undefined): string {
  const trimmed = name?.trim();
  if (trimmed) return trimmed;
  return mmsi ? `MMSI ${mmsi}` : "Unnamed";
}

function encounterLabel(enc: GfwEncounter): string {
  return `${partyName(enc.vessel.name, enc.vessel.mmsi)} × ${partyName(enc.other.name, enc.other.mmsi)}`;
}

/** What the map draws for this view: all ten hotspots; the table lists the top five. */
export function radarMapView(
  unseen: HormuzUnseenFile,
  history: HormuzHistoryFile,
  selectedEncounter: number | null,
): RadarMapView {
  return {
    cells: unseen.grid,
    encounters: history.encounters.map((enc) => ({
      lat: enc.lat,
      lon: enc.lon,
      label: `${encounterLabel(enc)} · ${fmtDay(enc.start)}`,
    })),
    hotspots: history.hotspots.map((spot) => ({
      lat: spot.lat,
      lon: spot.lon,
      label: `Loitering hotspot · ${spot.n} events`,
    })),
    selectedEncounter,
  };
}

/** The bounding box of the radar grid, for the fly-to. */
export function radarBounds(unseen: HormuzUnseenFile): [number, number, number, number] {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const cell of unseen.grid) {
    w = Math.min(w, cell.lon);
    e = Math.max(e, cell.lon);
    s = Math.min(s, cell.lat);
    n = Math.max(n, cell.lat);
  }
  if (!Number.isFinite(w)) return [54, 22, 60.5, 27.5];
  return [w - 0.05, s - 0.05, e + 0.05, n + 0.05];
}

export interface RadarPanelOptions {
  unseen: HormuzUnseenFile;
  history: HormuzHistoryFile;
  /** The day the panel rests on: the alert's day, or the latest well-covered one. */
  restDay: string | null;
  autoplay: boolean;
  /** 0..1 scale for the radar layer on the map. */
  onIntensity: (k: number) => void;
  onEncounter: (index: number) => void;
  onHotspot: (index: number) => void;
}

export interface RadarPanel {
  element: HTMLElement;
  /** Ring the row of one encounter (the map was clicked). */
  markEncounter(index: number | null): void;
  destroy(): void;
}

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  return el;
}

/** The latest day the median counts, so the panel has an honest default. */
export function latestWellCoveredDay(unseen: HormuzUnseenFile): string | null {
  const excluded = new Set(unseen.summary.excluded_days ?? []);
  for (let i = unseen.daily.length - 1; i >= 0; i -= 1) {
    const day = unseen.daily[i];
    if (day !== undefined && !excluded.has(day.day)) return day.day;
  }
  return unseen.daily.at(-1)?.day ?? null;
}

export function buildRadarPanel(options: RadarPanelOptions): RadarPanel {
  const { unseen, history } = options;
  const daily = unseen.daily;
  const excluded = new Set(unseen.summary.excluded_days ?? []);
  const median = unseen.summary.headline_share ?? unseen.summary.median_dark_share ?? null;
  const used = unseen.summary.n_days_used ?? daily.length - excluded.size;
  const maxDark = Math.max(1, ...daily.map((day) => day.dark));
  const maxShare = Math.max(0.01, ...daily.map((day) => day.dark_share));
  const restIndex = Math.max(
    0,
    daily.findIndex((day) => day.day === options.restDay),
  );

  let selected = restIndex;
  let timer: number | null = null;
  let destroyed = false;
  let playing = false;

  // ---- numbers ----------------------------------------------------------------

  const vDay = h("span", { class: "watch-mono" });
  const vCoverage = h("span", { class: "watch-rel" });
  const vSeen = h("span", { class: "watch-mono" });
  const vBroadcasting = h("span", { class: "watch-mono" });
  const vDark = h("span", { class: "watch-mono watch-radar-dark" });
  const vShare = h("span", { class: "watch-mono" });
  const fields = h(
    "dl",
    { class: "watch-fields" },
    h("dt", null, "Day"),
    h("dd", null, vDay, vCoverage),
    h("dt", null, "Radar-seen"),
    h("dd", null, vSeen),
    h("dt", null, "Broadcasting"),
    h("dd", null, vBroadcasting),
    h("dt", null, "Not broadcasting"),
    h("dd", null, vDark),
    h("dt", null, "Share"),
    h("dd", null, vShare),
    h("dt", null, "Median"),
    h(
      "dd",
      null,
      h(
        "span",
        { class: "watch-time-cell" },
        h("span", { class: "watch-mono" }, median === null ? "—" : pct(median)),
        h("span", { class: "watch-rel" }, `${used} well-covered days`),
      ),
    ),
  );

  // ---- chart ------------------------------------------------------------------

  const W = 340;
  const H = 54;
  const slot = W / Math.max(1, daily.length);
  const svg = svgEl("svg", {
    class: "watch-radar-chart",
    viewBox: `0 0 ${W} ${H + 12}`,
    role: "group",
    "aria-label": "Share of radar-seen vessels not broadcasting, per day",
  });
  const bars: SVGRectElement[] = [];
  daily.forEach((day, i) => {
    const barH = Math.max(1, (day.dark_share / maxShare) * H);
    const covered = !excluded.has(day.day);
    const bar = svgEl("rect", {
      x: i * slot + 0.5,
      y: H - barH,
      width: Math.max(1, slot - 1),
      height: barH,
      class: covered ? "watch-radar-bar" : "watch-radar-bar is-muted",
    });
    bars.push(bar);
    const hit = svgEl("rect", {
      x: i * slot,
      y: 0,
      width: slot,
      height: H,
      class: "watch-radar-hit",
      tabindex: 0,
      role: "button",
      "aria-label": `${fmtDay(day.day)}: ${pct(day.dark_share)} not broadcasting`,
    });
    const title = svgEl("title", {});
    title.textContent = `${fmtDay(day.day)} · ${day.dark} of ${day.seen} · ${pct(day.dark_share)}${covered ? "" : " · low coverage"}`;
    hit.append(title);
    hit.addEventListener("click", () => {
      stop();
      select(i, true);
    });
    hit.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      stop();
      select(i, true);
    });
    svg.append(bar, hit);
  });
  if (median !== null) {
    const y = H - (median / maxShare) * H;
    svg.append(svgEl("line", { x1: 0, x2: W, y1: y, y2: y, class: "watch-radar-median" }));
  }
  const playhead = svgEl("rect", { y: 0, height: H, width: Math.max(1.5, slot), class: "watch-radar-playhead" });
  svg.append(playhead);
  const first = daily[0];
  const last = daily.at(-1);
  const axisFirst = svgEl("text", { x: 0, y: H + 10, class: "watch-radar-axis" });
  axisFirst.textContent = first === undefined ? "" : fmtDay(first.day);
  const axisLast = svgEl("text", { x: W, y: H + 10, class: "watch-radar-axis", "text-anchor": "end" });
  axisLast.textContent = last === undefined ? "" : fmtDay(last.day);
  svg.append(axisFirst, axisLast);

  const playBtn = h("button", { class: "watch-radar-play", type: "button" }, `Play ${daily.length} days`);

  // ---- behaviour ----------------------------------------------------------------

  function paintDay(index: number): void {
    const day = daily[index];
    if (day === undefined) return;
    const isExcluded = excluded.has(day.day);
    vDay.textContent = fmtDay(day.day);
    vCoverage.textContent = isExcluded ? "low coverage, not in median" : "";
    vSeen.textContent = String(day.seen);
    vBroadcasting.textContent = String(day.broadcasting);
    vDark.textContent = String(day.dark);
    vShare.textContent = pct(day.dark_share);
    const x = index * slot;
    playhead.setAttribute("x", String(x));
    bars.forEach((bar, i) => bar.classList.toggle("is-selected", i === index));
    options.onIntensity(Math.max(0.08, day.dark / maxDark));
  }

  function select(index: number, fromUser: boolean): void {
    selected = index;
    paintDay(index);
    if (fromUser) playBtn.textContent = `Play ${daily.length} days`;
  }

  function stop(): void {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    playing = false;
  }

  function play(): void {
    stop();
    if (daily.length === 0) return;
    playing = true;
    playBtn.textContent = "Stop";
    let i = 0;
    paintDay(0);
    timer = window.setInterval(() => {
      if (destroyed) return;
      i += 1;
      if (i >= daily.length) {
        stop();
        select(restIndex, false);
        playBtn.textContent = "Replay";
        return;
      }
      selected = i;
      paintDay(i);
    }, PLAY_STEP_MS);
  }

  playBtn.addEventListener("click", () => {
    if (playing) {
      stop();
      select(selected, false);
      playBtn.textContent = "Replay";
    } else {
      play();
    }
  });

  // ---- meetings and hotspots ------------------------------------------------------

  const encRows: HTMLButtonElement[] = [];
  const encList = h("ol", { class: "watch-radar-list" });
  history.encounters.forEach((enc, i) => {
    const row = h(
      "button",
      { class: "watch-radar-row", type: "button" },
      h("span", { class: "watch-radar-row-name" }, encounterLabel(enc)),
      h("time", { class: "watch-mono watch-radar-row-time", datetime: enc.start }, fmtDay(enc.start)),
    );
    row.addEventListener("click", () => {
      markEncounter(i);
      options.onEncounter(i);
    });
    encRows.push(row);
    encList.append(h("li", null, row));
  });

  function markEncounter(index: number | null): void {
    encRows.forEach((row, i) => {
      if (i === index) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    });
    if (index !== null) encRows[index]?.scrollIntoView({ block: "nearest" });
  }

  const hotList = h("ol", { class: "watch-radar-list" });
  history.hotspots.slice(0, HOTSPOTS_SHOWN).forEach((spot, i) => {
    const row = h(
      "button",
      { class: "watch-radar-row", type: "button" },
      h(
        "span",
        { class: "watch-radar-row-name watch-mono" },
        `${spot.lat.toFixed(2)}°N ${spot.lon.toFixed(2)}°E`,
      ),
      h("span", { class: "watch-mono watch-radar-row-time" }, `${spot.n} events`),
    );
    row.addEventListener("click", () => options.onHotspot(i));
    hotList.append(h("li", null, row));
  });

  const total = history.counts?.encounters ?? history.encounters.length;
  const periodText =
    first !== undefined && last !== undefined ? `passes ${fmtDay(first.day)} – ${fmtDay(last.day)}` : "";

  const element = h(
    "div",
    { class: "watch-radar" },
    h(
      "div",
      { class: "watch-section-head" },
      h("h3", null, "Radar picture · Strait of Hormuz"),
    ),
    fields,
    h(
      "div",
      { class: "watch-radar-chartbox" },
      h("div", { class: "watch-radar-chart-head" }, h("span", null, "Not broadcasting, per day"), playBtn),
      svg,
      h("div", { class: "watch-radar-note" }, "Solid: well-covered day · shade on map scales with the day"),
    ),
    h("div", { class: "watch-section-head" }, h("h3", null, `Ship-to-ship meetings (GFW): ${total}`)),
    encList,
    h("div", { class: "watch-section-head" }, h("h3", null, "Loitering hotspots (GFW)")),
    hotList,
    h(
      "div",
      { class: "watch-radar-foot" },
      h("div", null, "Some radar targets are small craft that don't have to broadcast"),
      h(
        "div",
        null,
        `Radar · Sentinel-1 via `,
        h("a", { href: GFW_URL, target: "_blank", rel: "noopener noreferrer" }, "Global Fishing Watch"),
        ` · ${periodText}`,
      ),
      h(
        "div",
        null,
        h("a", { href: GFW_URL, target: "_blank", rel: "noopener noreferrer" }, "Powered by Global Fishing Watch"),
      ),
    ),
  );

  paintDay(selected);
  if (options.autoplay && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    play();
  } else {
    playBtn.textContent = `Play ${daily.length} days`;
  }

  return {
    element,
    markEncounter,
    destroy(): void {
      destroyed = true;
      stop();
    },
  };
}
