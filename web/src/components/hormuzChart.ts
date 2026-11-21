/**
 * The Hormuz block's one small chart: dark share per day for the whole window.
 *
 * A plain SVG polyline, 0-100%, with the period printed under the first and last
 * day. When the snapshot summary carries a robust median or an IQR it is drawn
 * as a dashed reference / band; otherwise only the daily series is shown.
 * Colours are CSS classes so they come from tokens.css.
 */

import type { HormuzDay, HormuzSummary } from "./hormuzTypes";

const SVG_NS = "http://www.w3.org/2000/svg";
const WIDTH = 400;
const HEIGHT = 104;
const PAD = { left: 30, right: 8, top: 8, bottom: 18 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

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

export function hormuzChart(daily: HormuzDay[], summary: HormuzSummary | undefined): SVGSVGElement {
  const first = daily[0];
  const last = daily[daily.length - 1];
  const label =
    first && last
      ? `${daily.length} days of radar-only share, ${dayLabel(first.day)} to ${dayLabel(last.day)}`
      : "Daily radar-only share";

  const chart = svgEl("svg", {
    viewBox: `0 0 ${WIDTH} ${HEIGHT}`,
    class: "hormuz-chart",
    role: "img",
    "aria-label": label,
  });

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (index: number, count: number): number =>
    PAD.left + (count <= 1 ? plotW / 2 : (index / (count - 1)) * plotW);
  const y = (value: number): number => PAD.top + (1 - value) * plotH;

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

  const points = daily
    .map((row, index) => {
      const value = share(row.dark_share);
      return value === null ? null : { x: x(index, daily.length), y: y(value) };
    })
    .filter((point): point is { x: number; y: number } => point !== null);

  if (points.length === 0) {
    chart.append(text("No daily radar data.", WIDTH / 2, HEIGHT / 2, "middle"));
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

  const line = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
    .join(" ");
  chart.append(
    svgEl("path", {
      d: `${line} L${points[points.length - 1]?.x ?? PAD.left} ${y(0)} L${points[0]?.x ?? PAD.left} ${y(0)} Z`,
      class: "area",
    }),
  );
  chart.append(svgEl("path", { d: line, class: "line" }));

  const reference = share(summary?.headline_share ?? summary?.median);
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

  if (first) chart.append(text(dayLabel(first.day), PAD.left, HEIGHT - 4, "start"));
  if (last) chart.append(text(dayLabel(last.day), WIDTH - PAD.right, HEIGHT - 4, "end"));

  return chart;
}
