/**
 * The Hormuz chart: dark share per day for the whole window.
 *
 * A plain SVG polyline, 0-100%, with the period printed under the first and last
 * day. When the snapshot summary carries a robust median or an IQR it is drawn
 * as a dashed reference / band; otherwise only the daily series is shown.
 * Colours are CSS classes so they come from tokens.css.
 *
 * Low-coverage days do not join the line: their share is mostly noise, so they
 * are drawn as faint points and the usable days on either side break apart. The
 * overview block passes no options and keeps the plain continuous line; the
 * focus view names the excluded days and asks for a taller chart.
 */

import type { HormuzDay, HormuzSummary } from "./hormuzTypes";

const SVG_NS = "http://www.w3.org/2000/svg";
const WIDTH = 400;
const DEFAULT_HEIGHT = 104;
const PAD = { left: 30, right: 8, top: 8, bottom: 18 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface HormuzChartOptions {
  /** Days the headline left out; drawn faint and never joined by the line. */
  excludedDays?: readonly string[];
  /** ViewBox height. Width stays 400 so the chart scales to its column. */
  height?: number;
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

/** A share is drawn only when it is a real 0..1 number. */
function share(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(1, Math.max(0, value))
    : null;
}

interface Point {
  x: number;
  y: number;
}

export function hormuzChart(
  daily: HormuzDay[],
  summary: HormuzSummary | undefined,
  options: HormuzChartOptions = {},
): SVGSVGElement {
  const height = Math.max(DEFAULT_HEIGHT, options.height ?? DEFAULT_HEIGHT);
  const excluded = new Set(options.excludedDays ?? []);
  const first = daily[0];
  const last = daily[daily.length - 1];

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const x = (index: number, count: number): number =>
    PAD.left + (count <= 1 ? plotW / 2 : (index / (count - 1)) * plotW);
  const y = (value: number): number => PAD.top + (1 - value) * plotH;

  // Split the series into runs of usable days. A low-coverage day ends a run
  // rather than joining across it, so the line never implies a reading there.
  const runs: Point[][] = [];
  const faint: Point[] = [];
  let run: Point[] = [];
  daily.forEach((row, index) => {
    const value = share(row.dark_share);
    const isExcluded = excluded.has(row.day);
    if (value === null || isExcluded) {
      if (run.length > 0) {
        runs.push(run);
        run = [];
      }
      if (value !== null && isExcluded) faint.push({ x: x(index, daily.length), y: y(value) });
      return;
    }
    run.push({ x: x(index, daily.length), y: y(value) });
  });
  if (run.length > 0) runs.push(run);

  const counted = runs.reduce((total, points) => total + points.length, 0);
  const label =
    first && last
      ? `${daily.length} days of radar-only share, ${dayLabel(first.day)} to ${dayLabel(last.day)}` +
        (excluded.size > 0
          ? `; ${counted} days counted, ${excluded.size} low-coverage days drawn faint`
          : "")
      : "Daily radar-only share";

  const chart = svgEl("svg", {
    viewBox: `0 0 ${WIDTH} ${height}`,
    class: "hormuz-chart",
    role: "img",
    "aria-label": label,
  });

  // 0 / 50 / 100% graticule.
  for (const level of [0, 0.5, 1]) {
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
  chart.append(text("100%", PAD.left - 6, y(1) + 3, "end"));
  chart.append(text("50%", PAD.left - 6, y(0.5) + 3, "end"));
  chart.append(text("0", PAD.left - 6, y(0) + 3, "end"));

  if (counted === 0) {
    chart.append(text("No daily radar data.", WIDTH / 2, height / 2, "middle"));
    return chart;
  }

  const bandLow = share(summary?.iqr?.[0]);
  const bandHigh = share(summary?.iqr?.[1]);
  if (bandLow !== null && bandHigh !== null) {
    const top = y(bandHigh);
    chart.append(
      svgEl("rect", {
        x: PAD.left,
        y: top,
        width: plotW,
        height: Math.max(1, y(bandLow) - top),
        class: "band",
      }),
    );
  }

  for (const points of runs) {
    if (points.length === 1) {
      chart.append(svgEl("circle", { cx: points[0].x, cy: points[0].y, r: 1.8, class: "dot" }));
      continue;
    }
    const line = points
      .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
      .join(" ");
    // With low-coverage days the runs are short and an area under each one reads
    // as a bar chart; the focus view keeps the line and the faint points only.
    if (excluded.size === 0) {
      chart.append(
        svgEl("path", {
          d: `${line} L${points[points.length - 1]?.x ?? PAD.left} ${y(0)} L${points[0]?.x ?? PAD.left} ${y(0)} Z`,
          class: "area",
        }),
      );
    }
    chart.append(svgEl("path", { d: line, class: "line" }));
  }

  for (const point of faint) {
    chart.append(svgEl("circle", { cx: point.x, cy: point.y, r: 1.6, class: "excluded" }));
  }

  const reference = share(summary?.headline_share ?? summary?.median_dark_share ?? summary?.median);
  if (reference !== null) {
    chart.append(
      svgEl("line", {
        x1: PAD.left,
        x2: WIDTH - PAD.right,
        y1: y(reference),
        y2: y(reference),
        class: "ref",
      }),
    );
  }

  if (first) chart.append(text(dayLabel(first.day), PAD.left, height - 4, "start"));
  if (last) chart.append(text(dayLabel(last.day), WIDTH - PAD.right, height - 4, "end"));

  return chart;
}
