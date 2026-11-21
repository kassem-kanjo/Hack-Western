/**
 * Area stats (#186): per-area alerts in the last 12 h and an hourly ship-count
 * sparkline, from `GET /api/areas/stats`. The Watch screen's area chips use these
 * to show more than a ship count; this module has no DOM beyond one SVG.
 */

const SPARK_WIDTH = 56;
const SPARK_HEIGHT = 14;
const SVG_NS = "http://www.w3.org/2000/svg";

export interface AreaStat {
  id: string;
  name: string;
  ships_now: number;
  /** Alert kind -> count, findings started in the last 12 h. */
  alerts_12h: Record<string, number>;
  /** Twelve hourly ship counts, oldest first. */
  sparkline: number[];
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Decode one API row defensively: a bad field cannot break the chip. */
function toAreaStat(raw: unknown): AreaStat | null {
  if (raw === null || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const id = record["id"];
  const name = record["name"];
  if (typeof id !== "string" || typeof name !== "string") return null;
  const alerts: Record<string, number> = {};
  const rawAlerts = record["alerts_12h"];
  if (rawAlerts !== null && typeof rawAlerts === "object") {
    for (const [kind, value] of Object.entries(rawAlerts as Record<string, unknown>)) {
      if (isNumber(value) && value > 0) alerts[kind] = value;
    }
  }
  const rawSpark = record["sparkline"];
  const sparkline = Array.isArray(rawSpark) ? rawSpark.filter(isNumber).slice(-12) : [];
  while (sparkline.length < 12) sparkline.unshift(0);
  const ships = record["ships_now"];
  return { id, name, ships_now: isNumber(ships) ? ships : 0, alerts_12h: alerts, sparkline };
}

/** Every area's stats; throws on a non-200 so the caller can keep the plain chips. */
export async function fetchAreaStats(signal?: AbortSignal): Promise<AreaStat[]> {
  const init: RequestInit = { headers: { Accept: "application/json" } };
  if (signal !== undefined) init.signal = signal;
  const response = await fetch("/api/areas/stats", init);
  if (!response.ok) throw new Error(`GET /api/areas/stats failed: HTTP ${response.status}`);
  const payload = (await response.json()) as { areas?: unknown };
  return Array.isArray(payload.areas)
    ? payload.areas.map(toAreaStat).filter((row): row is AreaStat => row !== null)
    : [];
}

export function totalAlerts(stat: AreaStat): number {
  return Object.values(stat.alerts_12h).reduce((sum, value) => sum + value, 0);
}

/** "dark 2 · meeting 1" for tooltips. */
export function alertBreakdown(stat: AreaStat): string {
  return Object.entries(stat.alerts_12h)
    .map(([kind, value]) => `${kind.replaceAll("_", " ")} ${value}`)
    .join(" · ");
}

/** A flat 1 px trace of the last twelve hourly ship counts. */
export function sparkline(values: number[]): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "watch-chip-spark");
  svg.setAttribute("viewBox", `0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`);
  svg.setAttribute("width", String(SPARK_WIDTH));
  svg.setAttribute("height", String(SPARK_HEIGHT));
  svg.setAttribute("aria-hidden", "true");
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? (SPARK_WIDTH - 2) / (values.length - 1) : 0;
  const points = values
    .map((value, index) => {
      const y = SPARK_HEIGHT - 1 - (value / max) * (SPARK_HEIGHT - 2);
      return `${(1 + index * step).toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const line = document.createElementNS(SVG_NS, "polyline");
  line.setAttribute("points", points);
  svg.append(line);
  return svg;
}
