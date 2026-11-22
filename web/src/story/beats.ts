/**
 * The six beats of the recorded story (#274): copy, timing and the one drawing
 * function each beat needs. The shell (story.ts) owns the map, the camera, the
 * clock of the rAF loop and the keyboard; a beat only writes its own ink.
 *
 * Copy is exact: where the brief fixes a sentence, it is written once here and
 * never assembled from other beats' numbers. The scenario payload's sub-line
 * is printed verbatim, so the case's numbers can never leak onto a line that
 * belongs to the scenario.
 */

import { h } from "../components/dom";
import { fmtInt } from "../format";
import { distanceKm } from "../map/watchMap";
import {
  circlePolygon,
  radiusKmAtMinutes,
  sliceTrail,
  trailCoords,
  trailPath,
  type LngLat,
  type StoryDot,
  type StoryMapLayers,
} from "./storyMap";
import type {
  EdgeView,
  SatcheckView,
  StoryCase,
  StoryContact,
  TrendView,
} from "./types";

/* ---- timing --------------------------------------------------------------- */

/** Beat 1: the recorded track draws in. */
export const TRAIL_MS = 3000;
/** Beats 2 and 3: the reach circle grows. */
export const REACH_MS = 4000;
/** Beat 4: the detections come up. */
export const DETECTIONS_MS = 2000;
/** Beat 5: the Pi state comes up. */
export const STATE_MS = 2000;
/** Beat 6: the call to action comes up. */
export const REVEAL_MS = 2000;

/* ---- what a beat may touch ------------------------------------------------ */

/** The text card, owned by story.ts; a beat writes into it and nothing else. */
export interface BeatUi {
  setHeadline(text: string): void;
  setSub(text: string): void;
  /** The scenario banner above the headline; null hides the strip. */
  setStrip(text: string | null): void;
  /** The single container below the sub-line (clock, cards, state, buttons). */
  extra(): HTMLElement;
  /** One HTML label on the map, positioned by the shell on every camera move. */
  setLabel(text: string, at: LngLat | null): void;
}

export interface BeatContext {
  story: StoryCase;
  layers: StoryMapLayers;
  ui: BeatUi;
  /** null while the satcheck rule is still running. */
  satcheck: SatcheckView | null;
  /** null while /api/edge/status is still in flight. */
  edge: EdgeView | null;
  /** null when the trend fetch failed or has not answered yet. */
  trend: TrendView | null;
  /** One rAF loop for this beat; canceled by the shell on beat change/destroy. */
  animate(durationMs: number, onTick: (eased: number) => void, onDone?: () => void): void;
}

export interface BeatSpec {
  index: number;
  /** What the camera frames when the beat opens. */
  camera: "case" | "satcheck" | "keep";
  durationMs: number;
  draw(ctx: BeatContext): void;
}

/* ---- copy helpers --------------------------------------------------------- */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function shipName(story: StoryCase): string {
  const trimmed = story.alert.name?.trim() ?? "";
  return trimmed.length > 0 ? trimmed : "This ship";
}

function hhmm(iso: string): string {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "";
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

/** "21:40 on 18 Nov", UTC. Null when the scene carries no parsable time. */
function passTime(iso: string | null): string | null {
  if (iso === null) return null;
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return null;
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${hhmm(iso)} on ${day} ${MONTHS[date.getUTCMonth()]}`;
}

function speedText(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function clockText(elapsedMinutes: number, radiusKm: number): string {
  const whole = Math.max(0, Math.floor(elapsedMinutes));
  const hours = Math.floor(whole / 60);
  const minutes = whole % 60;
  return `+${hours} h ${minutes} min \u00b7 up to ${Math.round(radiusKm)} km`;
}

function listed(contact: StoryContact): boolean {
  return contact.watchlist === "sanctioned" || contact.watchlist === "shadow_fleet";
}

function contactName(contact: StoryContact): string {
  const trimmed = contact.name?.trim() ?? "";
  return trimmed.length > 0 ? trimmed : `Unnamed ship, MMSI ${contact.mmsi}`;
}

function listLabel(contact: StoryContact): string {
  const named = contact.dossier?.watchlist?.list_name?.trim() ?? "";
  if (named.length > 0) return named;
  return contact.watchlist === "shadow_fleet" ? "Shadow-fleet list" : "Sanctions list";
}

/** The circle's radius at `eased`, clamped to what the evidence says has elapsed. */
function radiusFor(story: StoryCase, eased: number): number {
  const minutes = Math.max(0, story.alert.evidence?.minutes ?? 0);
  const raw = radiusKmAtMinutes(story.alert.speed_cap_kn, minutes * eased);
  const cap = Number.isFinite(story.reach_km) && story.reach_km > 0 ? story.reach_km : Infinity;
  return Math.min(raw, cap);
}

function centreDot(story: StoryCase, layers: StoryMapLayers): StoryDot[] {
  const { lat, lon } = story.alert.last;
  return [
    { lon, lat, color: layers.palette.dark, radius: 10, opacity: 0.16 },
    { lon, lat, color: layers.palette.dark, radius: 4, opacity: 1 },
  ];
}

/* ---- beat 1: the recorded track ------------------------------------------- */

function drawBeat1(ctx: BeatContext): void {
  const { story, ui, layers } = ctx;
  const alert = story.alert;
  const last: LngLat = [alert.last.lon, alert.last.lat];
  const stamp = hhmm(alert.last.t);
  const path = trailPath(trailCoords(alert.trail, alert.last));

  ui.setHeadline(`${shipName(story)} switched off its transponder`);
  ui.setSub(`Its track, as recorded. Last report ${stamp} UTC.`);
  ui.setStrip(null);
  ui.setLabel(`Last report ${stamp} UTC`, last);

  const paint = (eased: number): void => {
    layers.setTrail(sliceTrail(path, eased));
    layers.setDots(centreDot(story, layers));
  };
  paint(0);
  ctx.animate(TRAIL_MS, paint, () => paint(1));
}

/* ---- beat 2: the circle grows ---------------------------------------------- */

function drawBeat2(ctx: BeatContext): void {
  const { story, ui, layers } = ctx;
  const alert = story.alert;
  const centre: LngLat = [alert.last.lon, alert.last.lat];
  const minutes = Math.max(0, alert.evidence?.minutes ?? 0);

  ui.setHeadline("Since then it could have gone anywhere in this circle");
  ui.setSub(`Top speed ${speedText(alert.speed_cap_kn)} knots, times the time since its last report.`);
  ui.setStrip(null);

  const clock = h("div", { class: "story-clock" });
  ui.extra().append(clock);

  const paint = (eased: number): void => {
    const radius = radiusFor(story, eased);
    layers.setRing(radius > 0.05 ? circlePolygon(centre, radius) : null);
    layers.setDots(centreDot(story, layers));
    clock.textContent = clockText(minutes * eased, radius);
  };
  paint(0);
  ctx.animate(REACH_MS, paint, () => paint(1));
}

/* ---- beat 3: who was in reach ---------------------------------------------- */

function appendContactCard(host: HTMLElement, contact: StoryContact): void {
  const imo = contact.dossier?.imo;
  const card = h(
    "article",
    { class: "story-contact" },
    h("div", { class: "story-contact-name" }, contactName(contact)),
    h("div", { class: "story-contact-line" }, contact.watchlist === "shadow_fleet" ? "On a shadow-fleet list" : "On a sanctions list: Yes"),
    h("div", { class: "story-contact-list" }, listLabel(contact)),
    h(
      "div",
      { class: "story-contact-imo" },
      typeof imo === "number" && Number.isFinite(imo)
        ? `matched by IMO ${imo}`
        : "IMO not in our records",
    ),
  );
  host.append(card);
}

function drawBeat3(ctx: BeatContext): void {
  const { story, ui, layers } = ctx;
  const alert = story.alert;
  const centre: LngLat = [alert.last.lon, alert.last.lat];

  const contacts = (story.contacts ?? []).filter(
    (contact) => contact.could_meet === true && Number.isFinite(contact.lat) && Number.isFinite(contact.lon),
  );
  const listedCount = contacts.filter(listed).length;

  ui.setHeadline("Ships it could have met");
  ui.setSub(`${contacts.length} ships were in that circle. ${listedCount} ${listedCount === 1 ? "is" : "are"} on a sanctions or shadow-fleet list.`);
  ui.setStrip(null);

  const host = ui.extra();
  const background = (story.background ?? []).filter(
    (ship) => Number.isFinite(ship.lat) && Number.isFinite(ship.lon),
  );
  const distances = new Map<number, number>();
  for (const contact of contacts) {
    distances.set(contact.mmsi, distanceKm(alert.last.lat, alert.last.lon, contact.lat, contact.lon));
  }
  const carded = new Set<number>();

  const paint = (eased: number): void => {
    const radius = radiusFor(story, eased);
    layers.setRing(radius > 0.05 ? circlePolygon(centre, radius) : null);

    const dots: StoryDot[] = centreDot(story, layers);
    for (const ship of background) {
      dots.push({
        lon: ship.lon,
        lat: ship.lat,
        color: layers.palette.vesselDim,
        radius: 2,
        opacity: 1,
      });
    }

    for (const contact of contacts) {
      const distance = distances.get(contact.mmsi) ?? Infinity;
      const reached = distance <= radius;
      const isListed = listed(contact);
      let color = layers.palette.vessel;
      let opacity = 0.6;
      let dotRadius = 2.6;
      if (reached) {
        color = isListed ? layers.palette.sanctioned : layers.palette.dark;
        opacity = 1;
        dotRadius = isListed ? 4.2 : 3.6;
      } else if (isListed) {
        opacity = 0.75;
      }
      dots.push({ lon: contact.lon, lat: contact.lat, color, radius: dotRadius, opacity });
      if (reached && isListed && !carded.has(contact.mmsi)) {
        carded.add(contact.mmsi);
        appendContactCard(host, contact);
      }
    }

    layers.setDots(dots);
  };
  paint(0);
  ctx.animate(REACH_MS, paint, () => paint(1));
}

/* ---- beat 4: the satellite pass -------------------------------------------- */

function realSub(view: SatcheckView): string {
  const when = passTime(view.sceneTime);
  const lead = when === null ? "Radar saw the area." : `Radar pass ${when} over the area.`;
  return `${lead} ${view.counts.matched} ships matched to transponders, ${view.counts.not_broadcasting} not broadcasting.`;
}

function drawBeat4(ctx: BeatContext): void {
  const { ui, layers } = ctx;
  ui.setHeadline("A satellite passed over");

  const view = ctx.satcheck;
  if (view === null) {
    // The rule is still running. Never blank; the resolved sentence replaces this.
    ui.setStrip(null);
    ui.setSub("Checking for a satellite pass\u2026");
    return;
  }

  if (view.kind === "none") {
    ui.setStrip(null);
    ui.setSub("No satellite pass over this area yet.");
    return;
  }

  if (view.kind === "scenario") {
    ui.setStrip(view.banner !== null && view.banner.trim() !== "" ? view.banner : null);
    ui.setSub(
      view.sub !== null && view.sub.trim() !== "" ? view.sub : "No satellite pass over this area yet.",
    );
  } else {
    ui.setStrip(null);
    ui.setSub(realSub(view));
  }

  const extra = ui.extra();
  extra.append(
    h(
      "div",
      { class: "story-counts" },
      `${fmtInt(view.counts.detected)} detections \u00b7 ${fmtInt(view.counts.matched)} matched \u00b7 ${fmtInt(view.counts.not_broadcasting)} not broadcasting`,
    ),
  );
  if (view.source !== null && view.source.trim() !== "") {
    extra.append(h("div", { class: "story-source" }, view.source));
  }
  if (view.chips.length > 0) {
    const row = h("div", { class: "story-chips" });
    view.chips.forEach((src, index) => {
      row.append(
        h("img", {
          class: "story-chip",
          src,
          alt: `Radar chip, detection ${index + 1}`,
          width: 104,
          height: 72,
        }),
      );
    });
    extra.append(row);
  }

  const dots: StoryDot[] = [];
  for (const detection of view.detections) {
    if (!Number.isFinite(detection.lat) || !Number.isFinite(detection.lon)) continue;
    const matched = typeof detection.matched_mmsi === "number";
    dots.push({
      lon: detection.lon,
      lat: detection.lat,
      color: matched ? layers.palette.vessel : layers.palette.sanctioned,
      radius: matched ? 3 : 4.2,
      opacity: 1,
    });
  }
  const paint = (eased: number): void => {
    layers.setDots(dots.map((dot) => ({ ...dot, opacity: eased })));
  };
  paint(0);
  ctx.animate(DETECTIONS_MS, paint, () => paint(1));
}

/* ---- beat 5: the lookout --------------------------------------------------- */

function stateText(edge: EdgeView | null): string {
  if (edge === "ok") return "OK";
  if (edge === "blind") return "LOOKOUT BLIND";
  if (edge === "offline") return "OFFLINE";
  return "\u2026";
}

function drawBeat5(ctx: BeatContext): void {
  const { ui, edge } = ctx;

  ui.setHeadline("Then you send eyes");
  ui.setSub(
    "A camera lookout on a QNX Raspberry Pi. Alarm inside a 200 ms deadline: 63 of 64 on time under overload.",
  );
  ui.setStrip(null);

  const extra = ui.extra();
  const state = h(
    "div",
    { class: `story-state${edge === null ? " is-pending" : ` is-${edge}`}` },
    stateText(edge),
  );
  const deadline = h("div", { class: "story-deadline" }, "Alarm within 200 ms");
  const button = h("button", { class: "story-btn", type: "button" }, "Open the lookout");
  button.addEventListener("click", () => {
    location.hash = "#/patrol";
  });
  extra.append(state, deadline, button);

  state.style.opacity = "0";
  ctx.animate(
    STATE_MS,
    (eased) => {
      state.style.opacity = String(eased);
    },
    () => {
      state.style.opacity = "1";
    },
  );
}

/* ---- beat 6: Hormuz ------------------------------------------------------- */

function oneDecimalPercent(fraction: number): string {
  return (fraction * 100).toFixed(1);
}

function drawBeat6(ctx: BeatContext): void {
  const { story, ui, trend } = ctx;

  ui.setHeadline("In Hormuz, 2 in 3 ships weren't broadcasting");
  ui.setStrip(null);
  ui.setSub(
    trend === null
      ? "Share of ships seen by radar that were not broadcasting, Strait of Hormuz."
      : `Radar saw them; they were not broadcasting: ${oneDecimalPercent(trend.baseline)}% in 2025, ${oneDecimalPercent(trend.current)}% in Mar-Jun 2026.`,
  );

  const extra = ui.extra();
  const button = h("button", { class: "story-btn", type: "button" }, "Explore this ship");
  button.addEventListener("click", () => {
    location.hash = `#/watch/alert/${encodeURIComponent(story.alert.id)}`;
  });
  extra.append(button);

  button.style.opacity = "0";
  ctx.animate(
    REVEAL_MS,
    (eased) => {
      button.style.opacity = String(eased);
    },
    () => {
      button.style.opacity = "1";
    },
  );
}

/* ---- the sequence --------------------------------------------------------- */

export const BEATS: readonly BeatSpec[] = [
  { index: 0, camera: "case", durationMs: TRAIL_MS, draw: drawBeat1 },
  { index: 1, camera: "case", durationMs: REACH_MS, draw: drawBeat2 },
  { index: 2, camera: "case", durationMs: REACH_MS, draw: drawBeat3 },
  { index: 3, camera: "satcheck", durationMs: DETECTIONS_MS, draw: drawBeat4 },
  { index: 4, camera: "keep", durationMs: STATE_MS, draw: drawBeat5 },
  { index: 5, camera: "keep", durationMs: REVEAL_MS, draw: drawBeat6 },
];

export const BEAT_COUNT = BEATS.length;
