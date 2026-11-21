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
 *
 * Narrow containers (< 520 px) get a quieter drawing: 84 days at ~4 px spacing
 * read as spikes whatever the line does, so well-covered days become dots and a
 * centred 7-day rolling median carries the shape. Days the snapshot left out
 * are not drawn at all there, and only first / middle / last are labelled. The
 * layout is chosen from the measured container width and re-drawn (debounced)
 * when a ResizeObserver reports a change; from 520 px up the original drawing
 * is used untouched.
 */

import type { HormuzDay, HormuzSummary } from "./hormuzTypes";

const SVG_NS = "http://www.w3.org/2000/svg";
const WIDTH = 400;
const DEFAULT_HEIGHT = 104;
const PAD = { left: 30, right: 8, top: 8, bottom: 18 };
/** Narrow layout: room for a four-character "100%" at 11px mono. */
const NARROW_PAD = { left: 36, right: 10, top: 8, bottom: 18 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** Below this measured container width the chart switches to dots + median. */
const NARROW_WIDTH = 520;
/** Centred rolling median window, in days. */
const MEDIAN_WINDOW = 7;
const RESIZE_DEBOUNCE_MS = 120;

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

function text(
  value: string,
  x: number,
  y: number,
  anchor: "start" | "middle" | "end",
  className = "axis",
): SVGTextElement {
  const node = svgEl("text", { x, y, "text-anchor": anchor, class: className });
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

/** Median of a non-empty list; copies before sorting so callers keep their order. */
function medianOf(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

interface Point {
  x: number;
  y: number;
}

interface IndexedPoint extends Point {
  index: number;
}

export function hormuzChart(
  daily: HormuzDay[],
  summary: HormuzSummary | undefined,
  options: HormuzChartOptions = {},
): SVGSVGElement {
  const height = Math.max(DEFAULT_HEIGHT, options.height ?? DEFAULT_HEIGHT);
  const excluded = new Set(options.excludedDays ?? []);
  // The snapshot records every low-coverage day, even in views that do not
  // name them (the overview block passes no options). The narrow layout needs
  // the full set to know which days are well covered.
  const uncovered = new Set([...excluded, ...(summary?.excluded_days ?? [])]);
  const first = daily[0];
  const last = daily[daily.length - 1];

  const period =
    first && last
      ? `${daily.length} days of radar-only share, ${dayLabel(first.day)} to ${dayLabel(last.day)}`
      : "Daily radar-only share";

  const chart = svgEl("svg", {
    viewBox: `0 0 ${WIDTH} ${height}`,
    class: "hormuz-chart",
    role: "img",
  });

  /** Graticule and y labels; returns the y scale and the plot width. */
  const drawAxes = (pad: typeof PAD, labels: readonly [string, string, string]) => {
    const plotW = WIDTH - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const y = (value: number): number => pad.top + (1 - value) * plotH;

    // 0 / 50 / 100% graticule.
    for (const level of [0, 0.5, 1]) {
      chart.append(
        svgEl("line", {
          x1: pad.left,
          x2: WIDTH - pad.right,
          y1: y(level),
          y2: y(level),
          class: "grid",
        }),
      );
    }
    chart.append(text(labels[0], pad.left - 6, y(1) + 3, "end"));
    chart.append(text(labels[1], pad.left - 6, y(0.5) + 3, "end"));
    chart.append(text(labels[2], pad.left - 6, y(0) + 3, "end"));

    return { plotW, y };
  };

  const xScale = (pad: typeof PAD, plotW: number) => (index: number): number =>
    pad.left + (daily.length <= 1 ? plotW / 2 : (index / (daily.length - 1)) * plotW);

  /** The original drawing, for containers 520 px and wider. */
  const drawWide = (): void => {
    const { plotW, y } = drawAxes(PAD, ["100%", "50%", "0"]);
    const x = xScale(PAD, plotW);

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
        if (value !== null && isExcluded) faint.push({ x: x(index), y: y(value) });
        return;
      }
      run.push({ x: x(index), y: y(value) });
    });
    if (run.length > 0) runs.push(run);

    const counted = runs.reduce((total, points) => total + points.length, 0);
    chart.setAttribute(
      "aria-label",
      period +
        (excluded.size > 0
          ? `; ${counted} days counted, ${excluded.size} low-coverage days drawn faint`
          : ""),
    );

    if (counted === 0) {
      chart.append(text("No daily radar data.", WIDTH / 2, height / 2, "middle"));
      return;
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
  };

  /** Dots for well-covered days, a 7-day rolling median, three x labels. */
  const drawNarrow = (): void => {
    const { plotW, y } = drawAxes(NARROW_PAD, ["100%", "50%", "0%"]);
    const x = xScale(NARROW_PAD, plotW);

    // Per-day value, or null where the day is missing / low coverage.
    const values = daily.map((row) => (uncovered.has(row.day) ? null : share(row.dark_share)));
    const covered = values.filter((value): value is number => value !== null);
    chart.setAttribute(
      "aria-label",
      `${period}; ${covered.length} well-covered days as dots with a centred ${MEDIAN_WINDOW}-day rolling median`,
    );

    if (covered.length === 0) {
      chart.append(text("No daily radar data.", WIDTH / 2, height / 2, "middle"));
      return;
    }

    // Centred rolling median over the well-covered days in each 7-day window.
    // Excluded days contribute nothing, so the median can never be pulled by a
    // window that mostly missed its targets. A point is kept wherever the window
    // still holds a reading, which lets the line cross one- or two-day gaps.
    const half = Math.floor(MEDIAN_WINDOW / 2);
    const dots: IndexedPoint[] = [];
    const medianPoints: IndexedPoint[] = [];
    values.forEach((value, index) => {
      if (value !== null) dots.push({ index, x: x(index), y: y(value) });
      const from = Math.max(0, index - half);
      const to = Math.min(values.length - 1, index + half);
      const window: number[] = [];
      for (let cursor = from; cursor <= to; cursor += 1) {
        const reading = values[cursor];
        if (reading !== null) window.push(reading);
      }
      if (window.length === 0) return;
      medianPoints.push({ index, x: x(index), y: y(medianOf(window)) });
    });

    // One path per unbroken stretch; a gap with no well-covered day nearby is a
    // gap in the median too, never a straight jump across it.
    const segments: IndexedPoint[][] = [];
    let segment: IndexedPoint[] = [];
    for (const point of medianPoints) {
      const previous = segment[segment.length - 1];
      if (previous && point.index !== previous.index + 1) {
        segments.push(segment);
        segment = [];
      }
      segment.push(point);
    }
    if (segment.length > 0) segments.push(segment);

    for (const points of segments) {
      if (points.length < 2) continue;
      const line = points
        .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
        .join(" ");
      chart.append(svgEl("path", { d: line, class: "median" }));
    }

    for (const point of dots) {
      chart.append(svgEl("circle", { cx: point.x, cy: point.y, r: 2, class: "dot-min" }));
    }

    const reference = share(summary?.headline_share ?? summary?.median_dark_share ?? summary?.median);
    if (reference !== null) {
      chart.append(
        svgEl("line", {
          x1: NARROW_PAD.left,
          x2: WIDTH - NARROW_PAD.right,
          y1: y(reference),
          y2: y(reference),
          class: "ref",
        }),
      );
      chart.append(
        text(`${Math.round(reference * 100)}%`, WIDTH - NARROW_PAD.right, y(reference) - 4, "end"),
      );
    }

    // First, middle, last: three labels at most, deduped for very short series.
    const picks = [0, Math.floor((daily.length - 1) / 2), daily.length - 1];
    const placed = new Set<number>();
    for (const index of picks) {
      const day = daily[index];
      if (!day || placed.has(index)) continue;
      placed.add(index);
      const anchor = index === 0 ? "start" : index === daily.length - 1 ? "end" : "middle";
      chart.append(text(dayLabel(day.day), x(index), height - 4, anchor));
    }
  };

  let narrow = false;
  const render = (next: boolean): void => {
    narrow = next;
    chart.classList.toggle("is-narrow", narrow);
    chart.replaceChildren();
    if (narrow) drawNarrow();
    else drawWide();
  };

  render(false);

  // Choose the layout from the real container width, and re-draw when it
  // crosses the threshold. The observer is attached once the chart is in the
  // document; both callers append it in the same task the node is built in.
  if (typeof ResizeObserver === "function") {
    let pending: number | undefined;
    let latest = 0;

    const apply = (width: number): void => {
      if (width <= 0) return;
      const next = width < NARROW_WIDTH;
      if (next !== narrow) render(next);
    };

    const observer = new ResizeObserver((entries) => {
      if (!chart.isConnected) {
        observer.disconnect();
        return;
      }
      const entry = entries[entries.length - 1];
      if (!entry) return;
      latest = entry.contentRect.width;
      if (pending !== undefined) window.clearTimeout(pending);
      pending = window.setTimeout(() => {
        pending = undefined;
        apply(latest);
      }, RESIZE_DEBOUNCE_MS);
    });

    const attach = (): boolean => {
      const parent = chart.parentElement;
      if (!parent || !chart.isConnected) return false;
      observer.observe(parent);
      // Draw at the right size before the first paint instead of a frame of spikes.
      apply(parent.getBoundingClientRect().width);
      return true;
    };

    if (!attach()) queueMicrotask(attach);
  }

  return chart;
}
