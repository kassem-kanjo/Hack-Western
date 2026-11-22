/**
 * Satellite check for one dark alert (issue #268).
 *
 * The backend looks for a Sentinel-1 pass over the alert's search area and,
 * when one exists, reports every radar detection and whether our own AIS heard
 * a ship at that spot. This component owns that fetch and the panel section;
 * the map dots are drawn by the screen through `onPoints`, so the map module
 * still knows nothing about panels.
 *
 * Every field is read defensively: a missing or malformed body renders the one
 * fallback line instead of throwing, and a 404 means "not computed yet".
 */

import type { SatellitePoint } from "../map/watchMap";
import { h } from "./dom";

/** The one attribution line when the payload carries no `source`. */
export const DEFAULT_SATELLITE_SOURCE =
  "Contains modified Copernicus Sentinel-1 data, via Microsoft Planetary Computer";

export interface SatelliteCheckOptions {
  alertId: string;
  /**
   * Every detection as a map dot, or null to clear. `generatedAt` is the
   * check's own stamp, so the screen can skip a redraw when it is unchanged.
   */
  onPoints: (points: SatellitePoint[] | null, generatedAt: string | null) => void;
}

export interface SatelliteCheckHandle {
  element: HTMLElement;
  destroy(): void;
}

interface Detection {
  lat: number;
  lon: number;
  matched: boolean;
  chipUrl: string | null;
}

interface Check {
  generatedAt: string | null;
  status: "computed" | "no_scene";
  sceneTime: string | null;
  relation: "during" | "before" | "after" | null;
  minutesAfter: number | null;
  radiusKm: number | null;
  reachKm: number | null;
  capped: boolean;
  detected: number | null;
  notBroadcasting: number | null;
  detections: Detection[];
  notes: string[];
  source: string | null;
}

const NO_SCENE_LINE =
  "No Sentinel-1 radar pass over this area in the 48 hours before it switched off, or since.";
const NOT_COMPUTED_LINE = "No satellite check for this event yet.";
const LOAD_FAILED_LINE = "Satellite check could not be loaded.";
const LEGEND_HEARD = "Grey: heard by our AIS";
const LEGEND_NOT_BROADCASTING = "Red: not broadcasting";
const CHIPS_CAPTION = "Radar chips of ships not broadcasting (click to enlarge)";

/* ---- reading (pure, tolerant) --------------------------------------------- */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asRelation(value: unknown): "during" | "before" | "after" | null {
  return value === "during" || value === "before" || value === "after" ? value : null;
}

/** The payload, or null when it is not a satellite check this build can read. */
function parseSatelliteCheck(body: unknown): Check | null {
  const root = asRecord(body);
  if (root === null) return null;
  const status = root["status"];
  if (status !== "computed" && status !== "no_scene") return null;

  const scene = asRecord(root["scene"]);
  const area = asRecord(root["area"]);
  const counts = asRecord(root["counts"]);

  const detections: Detection[] = [];
  const rawDetections = Array.isArray(root["detections"]) ? root["detections"] : [];
  for (const raw of rawDetections) {
    const entry = asRecord(raw);
    if (entry === null) continue;
    const lat = asNumber(entry["lat"]);
    const lon = asNumber(entry["lon"]);
    if (lat === null || lon === null) continue;
    detections.push({
      lat,
      lon,
      matched: asNumber(entry["matched_mmsi"]) !== null,
      chipUrl: asText(entry["chip_url"]),
    });
  }

  const notes: string[] = [];
  const rawNotes = Array.isArray(root["notes"]) ? root["notes"] : [];
  for (const note of rawNotes) {
    const text = asText(note);
    if (text !== null) notes.push(text);
  }

  const sceneTime = scene === null ? null : asText(scene["time"]);
  const relation = asRelation(scene === null ? null : scene["relation"]);
  if (status === "computed" && (sceneTime === null || relation === null || counts === null)) {
    return null;
  }

  return {
    generatedAt: asText(root["generated_at"]),
    status,
    sceneTime,
    relation,
    minutesAfter: scene === null ? null : asNumber(scene["minutes_after_switch_off"]),
    radiusKm: area === null ? null : asNumber(area["radius_km"]),
    reachKm: area === null ? null : asNumber(area["reach_km"]),
    capped: area !== null && area["capped"] === true,
    detected: counts === null ? null : asNumber(counts["detected"]),
    notBroadcasting: counts === null ? null : asNumber(counts["not_broadcasting"]),
    detections: status === "computed" ? detections : [],
    notes,
    source: asText(root["source"]),
  };
}

/** "2026-11-21T05:12:00Z" -> "05:12", or null when the stamp is unusable. */
function clockUtc(iso: string | null): string | null {
  if (iso === null) return null;
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** "2026-11-21T05:12:00Z" -> "2026-11-21 05:12 UTC". */
function sceneStamp(iso: string | null): string | null {
  if (iso === null) return null;
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  const day = date.toISOString().slice(0, 10);
  const clock = clockUtc(iso);
  return clock === null ? null : `${day} ${clock} UTC`;
}

/** Minutes from the switch-off, as the short label the sentence uses. */
function gapLabel(minutes: number | null): string {
  if (minutes === null) return "";
  const value = Math.abs(minutes);
  if (value < 60) return `${Math.round(value)} min`;
  return `${Math.round(value / 60)} h`;
}

function whole(value: number): string {
  return String(Math.round(value));
}

/** The one plain sentence for a computed check. */
function summaryLine(check: Check): string {
  if (check.status === "no_scene") return NO_SCENE_LINE;

  const clock = clockUtc(check.sceneTime);
  const at = clock === null ? "" : ` at ${clock} UTC`;

  let relation: string;
  if (check.relation === "during") {
    relation = ", while it was dark";
  } else {
    const gap = gapLabel(check.minutesAfter);
    const side = check.relation === "before" ? "before" : "after";
    relation = gap === "" ? `, ${side} it switched off` : `, ${gap} ${side} it switched off`;
  }

  const seen = whole(check.detected ?? 0);
  if (check.notBroadcasting === null) {
    return `Satellite radar passed over this area${at}${relation}: ${seen} ships seen. No AIS from our receivers to compare.`;
  }
  return `Satellite radar passed over this area${at}${relation}: ${seen} ships seen, ${whole(check.notBroadcasting)} not broadcasting.`;
}

/** The small mono line: the reach circle that was searched, then the scene. */
function metaLine(check: Check): string | null {
  if (check.status !== "computed") return null;
  const parts: string[] = [];
  if (check.radiusKm !== null) {
    const circle = `Reach circle ${whole(check.radiusKm)} km`;
    parts.push(
      check.capped && check.reachKm !== null
        ? `${circle} (capped; the reach is ${whole(check.reachKm)} km)`
        : circle,
    );
  }
  const stamp = sceneStamp(check.sceneTime);
  if (stamp !== null) parts.push(`Scene ${stamp}`);
  return parts.length === 0 ? null : parts.join(" \u00b7 ");
}

function hook(chipUrl: string, alt: string): HTMLElement {
  return h(
    "a",
    { class: "satcheck-chip", href: chipUrl, target: "_blank", rel: "noopener" },
    h("img", { src: chipUrl, alt, loading: "lazy" }),
  );
}

/* ---- mount ---------------------------------------------------------------- */

export function mountSatelliteCheck(opts: SatelliteCheckOptions): SatelliteCheckHandle {
  const body = h("div", { class: "satcheck-body" });
  const element = h(
    "section",
    { class: "watch-nearby satcheck" },
    h("div", { class: "watch-section-head" }, h("h3", null, "Satellite check")),
    body,
  );

  const controller = new AbortController();
  let destroyed = false;

  function attribution(source: string | null): HTMLElement {
    return h("p", { class: "satcheck-source" }, source ?? DEFAULT_SATELLITE_SOURCE);
  }

  function renderLine(text: string, source?: string | null): void {
    if (destroyed) return;
    body.replaceChildren(h("p", { class: "satcheck-line" }, text), attribution(source ?? null));
  }

  function renderCheck(check: Check): void {
    if (destroyed) return;
    const clock = clockUtc(check.sceneTime);
    const children: HTMLElement[] = [h("p", { class: "satcheck-line" }, summaryLine(check))];

    const meta = metaLine(check);
    if (meta !== null) children.push(h("p", { class: "satcheck-mono satcheck-meta" }, meta));

    if (check.detections.length > 0) {
      children.push(
        h(
          "p",
          { class: "satcheck-legend" },
          h("span", { class: "satcheck-dot is-red", "aria-hidden": "true" }),
          LEGEND_NOT_BROADCASTING,
          h("span", { class: "satcheck-dot is-grey", "aria-hidden": "true" }),
          LEGEND_HEARD,
        ),
      );
    }

    const alt =
      clock === null
        ? "Radar chip of a ship not broadcasting"
        : `Radar chip of a ship not broadcasting at ${clock} UTC`;
    const chips: HTMLElement[] = [];
    for (const detection of check.detections) {
      if (detection.chipUrl !== null) chips.push(hook(detection.chipUrl, alt));
    }
    if (chips.length > 0) {
      children.push(
        h("div", { class: "satcheck-chips" }, ...chips),
        h("p", { class: "satcheck-caption" }, CHIPS_CAPTION),
      );
    }

    if (check.notes.length > 0) {
      children.push(...check.notes.map((note) => h("p", { class: "satcheck-note" }, note)));
    }

    children.push(attribution(check.source));
    body.replaceChildren(...children);
  }

  async function load(): Promise<void> {
    try {
      const response = await fetch(`/api/satcheck/${encodeURIComponent(opts.alertId)}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (destroyed) return;
      if (response.status === 404) {
        opts.onPoints(null, null);
        renderLine(NOT_COMPUTED_LINE);
        return;
      }
      if (!response.ok) throw new Error(`GET /api/satcheck failed: HTTP ${response.status}`);
      const check = parseSatelliteCheck((await response.json()) as unknown);
      if (check === null) throw new Error("satellite check payload not understood");
      if (destroyed) return;

      if (check.status === "no_scene") {
        opts.onPoints(null, null);
      } else {
        opts.onPoints(
          check.detections.map((detection) => ({
            lat: detection.lat,
            lon: detection.lon,
            matched: detection.matched,
          })),
          check.generatedAt,
        );
      }
      renderCheck(check);
    } catch {
      if (destroyed || controller.signal.aborted) return;
      opts.onPoints(null, null);
      renderLine(LOAD_FAILED_LINE);
    }
  }

  void load();

  return {
    element,
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      controller.abort();
    },
  };
}
