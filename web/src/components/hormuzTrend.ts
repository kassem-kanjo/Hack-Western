/**
 * Two years of weekly dark share (Hormuz against its Gulf of Oman control) and
 * the headline that reads the same regime block. Every number comes from
 * /api/hormuz/trend; nothing here is a constant except the strike date marker.
 */

import { h } from "./dom";

const SVG_NS = "http://www.w3.org/2000/svg";
// Drawn at the rail's real width (about 340 px) so the labels are 1:1, not scaled.
const W = 340;
const H = 150;
const PAD = { left: 30, right: 6, top: 14, bottom: 20 };

/** The day the strikes began and the strait closed (the one labelled marker). */
const STRIKE_DAY = "2026-02-28";

export interface TrendWeek {
  week: string;
  seen: number;
  dark: number;
  dark_share: number | null;
}

export interface TrendEvent {
  date_start: string;
  date_end: string;
  title: string;
  type: string;
  source_url: string;
}

export interface TrendRegime {
  treated: {
    baseline: { dark_share: number | null };
    current: { dark_share: number | null };
  };
  control: {
    baseline: { dark_share: number | null };
    current: { dark_share: number | null };
  };
  did: number | null;
  did_ci: [number, number] | null;
  verdict: string | null;
}

export interface HormuzTrend {
  treated: TrendWeek[];
  control: TrendWeek[];
  events: TrendEvent[];
  regime: TrendRegime | null;
}

function svg<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

const pct = (share: number | null | undefined): number | null =>
  typeof share === "number" && Number.isFinite(share) ? Math.round(share * 100) : null;

/** The headline sentence and its figures, read from the regime block only. */
export function trendHeadline(regime: TrendRegime): {
  sentence: string;
  big: string;
  before: string;
  control: string;
  difference: string;
} | null {
  const now = pct(regime.treated.current.dark_share);
  const was = pct(regime.treated.baseline.dark_share);
  const ctrlWas = pct(regime.control.baseline.dark_share);
  const ctrlNow = pct(regime.control.current.dark_share);
  if (now === null || was === null || ctrlWas === null || ctrlNow === null || regime.did === null) {
    return null;
  }
  const ci = regime.did_ci;
  const ciText = ci ? ` (95% CI ${Math.round(ci[0] * 100)}–${Math.round(ci[1] * 100)})` : "";
  return {
    sentence: `Since the war began, ${Math.round((now / 100) * 3)} in 3 ships here hide from tracking`,
    big: `${now}%`,
    before: `was ${was}% before 28 Feb`,
    control: `Gulf of Oman next door: ${ctrlWas}% → ${ctrlNow}%.`,
    difference: `Difference ${regime.did >= 0 ? "+" : ""}${(regime.did * 100).toFixed(1)} pts${ciText}`,
  };
}

/** Week-start ISO date to epoch ms, or NaN when unreadable. */
const ms = (day: string): number => Date.parse(`${day}T00:00:00Z`);

/** The weekly two-line chart. Hormuz is the state colour, the control is grey. */
export function trendChart(trend: HormuzTrend): HTMLElement {
  const weeks = [...trend.treated, ...trend.control].map((row) => ms(row.week)).filter(Number.isFinite);
  if (weeks.length === 0) {
    return h("p", { class: "trend-empty" }, "No weekly series in the snapshot yet.");
  }
  const x0 = Math.min(...weeks);
  const x1 = Math.max(...weeks);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const x = (t: number): number => PAD.left + ((t - x0) / Math.max(1, x1 - x0)) * plotW;
  const y = (share: number): number => PAD.top + (1 - share) * plotH;

  const root = svg("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "trend-svg",
    role: "img",
    "aria-label": "Weekly share of ships not broadcasting: Hormuz against the Gulf of Oman",
  });

  for (const share of [0, 0.5, 1]) {
    root.append(svg("line", { x1: PAD.left, x2: W - PAD.right, y1: y(share), y2: y(share), class: "trend-grid" }));
    const label = svg("text", { x: PAD.left - 6, y: y(share) + 3, class: "trend-axis", "text-anchor": "end" });
    label.textContent = `${share * 100}%`;
    root.append(label);
  }

  const line = (rows: TrendWeek[], cls: string): void => {
    const points = rows
      .map((row) => ({ t: ms(row.week), share: row.dark_share }))
      .filter((p): p is { t: number; share: number } => Number.isFinite(p.t) && typeof p.share === "number")
      .map((p) => `${x(p.t).toFixed(1)},${y(p.share).toFixed(1)}`);
    if (points.length > 1) root.append(svg("polyline", { points: points.join(" "), class: cls, fill: "none" }));
  };
  line(trend.control, "trend-control");
  line(trend.treated, "trend-treated");

  // Other events: faint ticks on the base line, title on hover.
  for (const event of trend.events) {
    const t = ms(event.date_start);
    if (!Number.isFinite(t) || event.date_start === STRIKE_DAY) continue;
    const tick = svg("line", { x1: x(t), x2: x(t), y1: H - PAD.bottom, y2: H - PAD.bottom - 6, class: "trend-tick" });
    const title = svg("title", {});
    title.textContent = `${event.date_start}: ${event.title}`;
    tick.append(title);
    root.append(tick);
  }

  // The one labelled marker.
  const strike = ms(STRIKE_DAY);
  if (Number.isFinite(strike) && strike >= x0 && strike <= x1) {
    root.append(svg("line", { x1: x(strike), x2: x(strike), y1: PAD.top, y2: H - PAD.bottom, class: "trend-marker" }));
    const label = svg("text", { x: x(strike) - 5, y: PAD.top + 10, class: "trend-marker-label", "text-anchor": "end" });
    label.textContent = "28 Feb 2026: strikes, strait closed";
    root.append(label);
  }

  // Axis: the first and last week, as month labels.
  for (const [t, anchor] of [[x0, "start"], [x1, "end"]] as const) {
    const label = svg("text", { x: t === x0 ? PAD.left : W - PAD.right, y: H - 6, class: "trend-axis", "text-anchor": anchor });
    label.textContent = new Date(t).toISOString().slice(0, 7);
    root.append(label);
  }

  return h("div", { class: "trend" }, root);
}
