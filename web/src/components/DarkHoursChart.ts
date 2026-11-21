/**
 * "Hours ships spent dark per day": the fleet-wide daily dark-hours aggregate
 * from Tiger Data, drawn as thin bars.
 *
 * The block fetches its own snapshot so the overview paint never waits on it,
 * and it removes itself when the aggregate is empty: no empty box, no zero
 * chart. Styling is colocated because the chart lives outside the rail's
 * existing sections; colours and type come from tokens.css.
 */

import type { AisHour, DarkHoursDay } from "../api";
import { fetchStats } from "../api";
import { h } from "./dom";

const SVG_NS = "http://www.w3.org/2000/svg";
const WIDTH = 400;
const HEIGHT = 104;
const PAD = { left: 32, right: 8, top: 8, bottom: 18 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const TITLE = "Hours ships spent dark per day";
const HOURLY_TITLE = "Messages our receivers recorded per hour";
const HOURLY_CAPTION =
  "Our AIS recording \u00b7 hourly continuous aggregate in Tiger Data (TimescaleDB), UTC";
const CAPTION =
  "Global Fishing Watch gap events \u00b7 daily continuous aggregate in Tiger Data (TimescaleDB)";

const STYLE_ID = "dark-hours-style";
const STYLE = `
.dark-hours h3 {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-h3);
  font-weight: var(--fw-medium);
  color: var(--c-text-1);
  letter-spacing: 0;
  text-transform: none;
}
.dark-hours .dh-chart {
  display: block;
  width: 100%;
  height: auto;
  margin: var(--sp-2) 0 0;
}
.dark-hours .dh-chart .grid {
  stroke: var(--c-line);
  stroke-width: 1;
}
.dark-hours .dh-chart .axis {
  fill: var(--c-text-3);
  font-family: var(--font-mono);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}
.dark-hours .dh-chart .dh-bar {
  fill: var(--c-gap);
  opacity: 0.75;
}
.dark-hours .caption {
  margin: var(--sp-2) 0 0;
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  line-height: 1.4;
  color: var(--c-text-3);
}
`;

export interface DarkHoursChartHandle {
  element: HTMLElement;
  destroy(): void;
}

interface DailyTotal {
  day: string;
  hours: number;
}

function ensureStyle(): void {
  if (document.getElementById(STYLE_ID) !== null) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = STYLE;
  document.head.append(style);
}

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number> = {},
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function text(value: string, x: number, y: number, anchor: "start" | "middle" | "end"): SVGTextElement {
  const node = svgEl("text", { x, y, "text-anchor": anchor, class: "axis" });
  node.textContent = value;
  return node;
}

/** "2026-08-15" -> "15 Aug". Parsed as UTC: the snapshot is UTC end to end. */
function dayLabel(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  return `${String(date).padStart(2, "0")} ${MONTHS[month - 1] ?? ""}`.trim();
}

/** Group the aggregate by day and sum it, so duplicate region rows cannot double-count. */
export function dailyTotals(days: readonly DarkHoursDay[]): DailyTotal[] {
  const totals = new Map<string, number>();
  for (const row of days) {
    if (typeof row.hours !== "number" || !Number.isFinite(row.hours)) continue;
    totals.set(row.day, (totals.get(row.day) ?? 0) + row.hours);
  }
  return [...totals.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([day, hours]) => ({ day, hours }));
}

/** "2026-11-21T04:00:00Z" -> "21 Nov 04:00". */
function hourLabel(hour: string): string {
  const [day = "", time = ""] = hour.split("T");
  return `${dayLabel(day)} ${time.slice(0, 5)}`.trim();
}

/** Hourly message counts as chart rows (same shape as daily totals). */
export function hourlyTotals(hours: readonly AisHour[]): DailyTotal[] {
  return hours
    .filter((row) => typeof row.msgs === "number" && Number.isFinite(row.msgs))
    .map((row) => ({ day: row.hour, hours: row.msgs }))
    .sort((left, right) => (left.day < right.day ? -1 : left.day > right.day ? 1 : 0));
}

/** The bar chart alone, or null when the aggregate has no days. */
export function darkHoursChart(
  days: readonly DarkHoursDay[],
  rows: DailyTotal[] = dailyTotals(days),
  title: string = TITLE,
  labelOf: (key: string) => string = dayLabel,
): SVGSVGElement | null {
  const totals = rows;
  const first = totals[0];
  const last = totals[totals.length - 1];
  if (first === undefined || last === undefined) return null;

  ensureStyle();

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const max = Math.max(...totals.map((row) => row.hours), 0);
  const top = max > 0 ? max : 1;
  const label = `${title}, ${totals.length} bars, ${labelOf(first.day)} to ${labelOf(last.day)}`;

  const chart = svgEl("svg", {
    viewBox: `0 0 ${WIDTH} ${HEIGHT}`,
    class: "dh-chart",
    role: "img",
    "aria-label": label,
  });

  const y = (value: number): number => PAD.top + (1 - value / top) * plotH;
  const slot = plotW / totals.length;
  // Thin, calm bars: most of each slot stays empty, so a fortnight reads as a rhythm.
  const barW = Math.max(1.5, Math.min(4, slot * 0.55));

  for (const level of [0, top]) {
    chart.append(
      svgEl("line", {
        x1: PAD.left,
        x2: WIDTH - PAD.right,
        y1: y(level),
        y2: y(level),
        class: "grid",
      }),
    );
  }
  chart.append(text(max > 0 ? String(Math.round(max)) : "0", PAD.left - 6, PAD.top + 3, "end"));
  chart.append(text("0", PAD.left - 6, y(0) + 3, "end"));

  totals.forEach((row, index) => {
    const centre = PAD.left + slot * (index + 0.5);
    const height = Math.max(0, y(0) - y(row.hours));
    chart.append(
      svgEl("rect", {
        x: centre - barW / 2,
        y: y(row.hours),
        width: barW,
        height: Math.max(height, row.hours > 0 ? 1 : 0),
        class: "dh-bar",
      }),
    );
  });

  chart.append(text(labelOf(first.day), PAD.left, HEIGHT - 4, "start"));
  chart.append(text(labelOf(last.day), WIDTH - PAD.right, HEIGHT - 4, "end"));

  return chart;
}

/**
 * The rail block: title, chart, caption, and nothing at all when the API has no
 * daily aggregate. Fetching here keeps the overview a synchronous paint.
 */
export function darkHoursBlock(): DarkHoursChartHandle {
  const element = h("section", { class: "rail-section dark-hours", hidden: true });
  const abort = new AbortController();

  void fetchStats(abort.signal)
    .then((stats) => {
      if (abort.signal.aborted || !element.isConnected) return;
      // Dark hours need GFW gap events in the window; where GFW logs none (narrow straits),
      // fall back to our own recording's hourly aggregate, which is always real.
      let chart = darkHoursChart(stats.dark_hours_daily ?? []);
      let title = TITLE;
      let caption = CAPTION;
      if (chart === null) {
        title = HOURLY_TITLE;
        caption = HOURLY_CAPTION;
        chart = darkHoursChart([], hourlyTotals(stats.ais_hourly ?? []), title, hourLabel);
      }
      if (chart === null) {
        element.remove();
        return;
      }
      element.replaceChildren(h("h3", null, title), chart, h("p", { class: "caption" }, caption));
      element.hidden = false;
    })
    .catch(() => {
      // A missing snapshot is not a console failure: the overview still works.
      if (!abort.signal.aborted) element.remove();
    });

  return {
    element,
    destroy(): void {
      abort.abort();
    },
  };
}
