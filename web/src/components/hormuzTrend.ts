/**
 * Two years of pooled dark share (Hormuz against its Gulf of Oman control) and
 * the headline that reads the same regime block. Every number comes from
 * /api/hormuz/trend; nothing here is a constant except the strike date marker.
 */

import { h } from "./dom";

const SVG_NS = "http://www.w3.org/2000/svg";
// Drawn at the rail's real width (about 340 px) so the labels are 1:1, not scaled.
const W = 340;
const H = 126;
// The right pad keeps room for the two direct labels, so neither sits on a line.
const PAD = { left: 30, right: 76, top: 18, bottom: 20 };

/** The day the strikes began and the strait closed: the one date the chart marks. */
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

export interface TrendWindow {
  start: string;
  end: string;
}

export interface TrendRegime {
  baseline?: TrendWindow;
  current?: TrendWindow;
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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Mar–Jun 2026" for a window inside one year, else "Mar 2026–Feb 2027". */
function windowLabel(w: TrendWindow | undefined): string | null {
  if (!w) return null;
  const a = new Date(`${w.start}T00:00:00Z`);
  const b = new Date(`${w.end}T00:00:00Z`);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null;
  const am = MONTHS[a.getUTCMonth()];
  const bm = MONTHS[b.getUTCMonth()];
  return a.getUTCFullYear() === b.getUTCFullYear()
    ? `${am}–${bm} ${b.getUTCFullYear()}`
    : `${am} ${a.getUTCFullYear()}–${bm} ${b.getUTCFullYear()}`;
}

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
  // Plain words, not a data team's shorthand: a signed point difference is a comparison,
  // and the interval is stated as our confidence in it.
  const ciText = ci
    ? ` (we're 95% sure it's between ${Math.round(ci[0] * 100)} and ${Math.round(ci[1] * 100)})`
    : "";
  const direction = regime.did < 0 ? "less" : "more";
  const difference = `${Math.abs(regime.did * 100).toFixed(1)} percentage points ${direction} than next door${ciText}`;
  // The 66% is the average of a fixed window after the strikes, not "today": say which months.
  const during = windowLabel(regime.current);
  const baseYear = regime.baseline ? regime.baseline.start.slice(0, 4) : null;
  return {
    sentence: during
      ? `After the war began (${during}), ${Math.round((now / 100) * 3)} in 3 ships here weren't broadcasting their position`
      : `After the war began, ${Math.round((now / 100) * 3)} in 3 ships here weren't broadcasting their position`,
    big: `${now}%`,
    before: baseYear ? `was ${was}% in ${baseYear}` : `was ${was}% before`,
    control: `Next door in the Gulf of Oman it only rose from ${ctrlWas}% to ${ctrlNow}%.`,
    difference,
  };
}

/** Week-start ISO date to epoch ms, or NaN when unreadable. */
const ms = (day: string): number => Date.parse(`${day}T00:00:00Z`);

/**
 * Pooled rolling share over `span` weeks: sum of dark over sum of seen. A point
 * is drawn only once a full window exists, so the first weeks never show a
 * partial average.
 */
function rolling(rows: TrendWeek[], span = 4): TrendWeek[] {
  const sorted = rows
    .filter((row) => Number.isFinite(ms(row.week)))
    .sort((a, b) => ms(a.week) - ms(b.week));
  return sorted.map((row, i) => {
    if (i < span - 1) return { ...row, dark_share: null };
    const window = sorted.slice(i - span + 1, i + 1);
    const seen = window.reduce((sum, w) => sum + w.seen, 0);
    const dark = window.reduce((sum, w) => sum + w.dark, 0);
    return { ...row, dark_share: seen > 0 ? dark / seen : null };
  });
}

/** The two-line chart. Hormuz is the state colour, the control is grey. */
export function trendChart(trend: HormuzTrend): HTMLElement {
  const weeks = [...trend.treated, ...trend.control].map((row) => ms(row.week)).filter(Number.isFinite);
  if (weeks.length === 0) {
    return h("p", { class: "trend-empty" }, "No weekly series in the snapshot yet.");
  }
  const x0 = Math.min(...weeks);
  const x1 = Math.max(...weeks);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const right = W - PAD.right;
  const x = (t: number): number => PAD.left + ((t - x0) / Math.max(1, x1 - x0)) * plotW;
  const y = (share: number): number => PAD.top + (1 - share) * plotH;

  const root = svg("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "trend-svg",
    role: "img",
    "aria-label": "Share of ships not broadcasting: Hormuz against the Gulf of Oman",
  });

  for (const share of [0, 0.5, 1]) {
    root.append(svg("line", { x1: PAD.left, x2: right, y1: y(share), y2: y(share), class: "trend-grid" }));
    const label = svg("text", { x: PAD.left - 6, y: y(share) + 3, class: "trend-axis", "text-anchor": "end" });
    label.textContent = `${share * 100}%`;
    root.append(label);
  }

  // The period the headline averages, drawn before the lines so it stays behind them.
  const strike = ms(STRIKE_DAY);
  const marked = Number.isFinite(strike) && strike >= x0 && strike <= x1;
  const now = trend.regime ? pct(trend.regime.treated.current.dark_share) : null;
  // Shade exactly the window the 66% averages (regime.current), not up to today.
  const win = trend.regime?.current;
  const ws = win ? ms(win.start) : NaN;
  const we = win ? ms(win.end) : NaN;
  if (now !== null && Number.isFinite(ws) && Number.isFinite(we) && we > x0 && ws < x1) {
    const left = x(Math.max(ws, x0));
    const rightEdge = x(Math.min(we, x1));
    root.append(svg("rect", { x: left, y: PAD.top, width: rightEdge - left, height: plotH, class: "trend-shade" }));
    // In the top padding, right-aligned to the shade: above the plot the lines never reach it.
    const note = svg("text", { x: rightEdge - 2, y: PAD.top - 6, class: "trend-note", "text-anchor": "end" });
    note.textContent = `${windowLabel(win) ?? "After the war began"}: ${now}% average`;
    root.append(note);
  }
  // The dashed line is the shade's left edge, and its only mark.
  if (marked) {
    root.append(svg("line", { x1: x(strike), x2: x(strike), y1: PAD.top, y2: H - PAD.bottom, class: "trend-marker" }));
  }

  /** Draw one pooled line; return the share at its right end, for the direct label. */
  const line = (rows: TrendWeek[], cls: string): number | null => {
    const points = rolling(rows)
      .map((row) => ({ t: ms(row.week), share: row.dark_share }))
      .filter((p): p is { t: number; share: number } => Number.isFinite(p.t) && typeof p.share === "number");
    if (points.length < 2) return null;
    const at = points.map((p) => `${x(p.t).toFixed(1)},${y(p.share).toFixed(1)}`);
    root.append(svg("polyline", { points: at.join(" "), class: cls, fill: "none" }));
    return points[points.length - 1]!.share;
  };
  // Pooled over four weeks (dark over seen across the window), so one quiet week
  // cannot draw a spike.
  const treated = line(trend.treated, "trend-treated");
  const control = line(trend.control, "trend-control");

  // Direct labels in the right-hand room: the name only, in the line's colour.
  // The two ends often sit within a few points of each other, so the lower label
  // is pushed down until the pair keeps a 12 px gap.
  const ends: { y: number; name: string; cls: string }[] = [];
  if (treated !== null) ends.push({ y: y(treated) + 3, name: "Hormuz", cls: "is-treated" });
  if (control !== null) ends.push({ y: y(control) + 3, name: "Gulf of Oman", cls: "is-control" });
  ends.sort((a, b) => a.y - b.y);
  ends.forEach((end, i) => {
    const previous = ends[i - 1];
    if (previous !== undefined) end.y = Math.max(end.y, previous.y + 12);
    const label = svg("text", { x: right + 4, y: end.y, class: `trend-label ${end.cls}`, "text-anchor": "start" });
    label.textContent = end.name;
    root.append(label);
  });

  // Axis: the first and last week, as month labels.
  for (const [t, anchor] of [[x0, "start"], [x1, "end"]] as const) {
    const label = svg("text", { x: x(t), y: H - 6, class: "trend-axis", "text-anchor": anchor });
    label.textContent = new Date(t).toISOString().slice(0, 7);
    root.append(label);
  }

  return h("div", { class: "trend" }, root);
}
