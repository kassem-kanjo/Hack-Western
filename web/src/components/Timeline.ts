/**
 * Bottom timeline: a native range scrubber through the gap, radar-pass markers
 * and a three-row readout. The frames come from the snapshot (`lens_frames`);
 * index 0 is the "off" ping and the last index is the "on" ping.
 */

import type { Candidate, CaseDetail } from "../api";
import { fmtOffset, fmtUtc } from "../format";
import { isCandidateLive } from "../model/frames";
import { h } from "./dom";

export interface TimelineHandle {
  element: HTMLElement;
  setFrame(index: number): void;
  destroy(): void;
}

interface Marker {
  element: HTMLElement;
  candidates: Candidate[];
}

const CAPTION =
  "Drag to move through the gap. The lens at time t is the overlap of two speed-limited circles.";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Fraction 0..1 of the way from `off` to `on`, clamped. */
function timeFraction(offIso: string, onIso: string, iso: string): number {
  const start = Date.parse(offIso);
  const end = Date.parse(onIso);
  const at = Date.parse(iso);
  if (!Number.isFinite(start) || !Number.isFinite(end) || !Number.isFinite(at)) return 0;
  const span = end - start;
  if (span <= 0) return 0;
  return clamp((at - start) / span, 0, 1);
}

const MAX_TICK_LABELS = 10;
const TICK_STEPS = [4, 6, 8, 12, 24, 48];

/**
 * Smallest step that keeps the hour labels under the cap. Four-hour ticks make
 * a 96 h gap unreadable; pick 8, 12 or 24 h instead. Anything longer falls back
 * to exactly ten labels.
 */
function tickStep(hours: number): number {
  for (const step of TICK_STEPS) {
    if (Math.floor(hours / step) <= MAX_TICK_LABELS) return step;
  }
  return Math.ceil(hours / MAX_TICK_LABELS);
}

/**
 * One marker per distinct radar scene time, numbered in time order.
 *
 * The label sits above the track on a short leader instead of inside the
 * diamond, and close passes get stacked into lanes so two labels that share a
 * timestamp (a single satellite pass covering several cells) never overlap.
 */
function radarMarkers(detail: CaseDetail, frames: CaseDetail["lens_frames"]): Marker[] {
  const byScene = new Map<string, Candidate[]>();

  for (const candidate of detail.candidates) {
    if (candidate.scene_time === null) continue;
    const existing = byScene.get(candidate.scene_time);
    if (existing) {
      existing.push(candidate);
    } else {
      byScene.set(candidate.scene_time, [candidate]);
    }
  }

  const scenes = [...byScene.keys()].sort((a, b) => Date.parse(a) - Date.parse(b));

  // Four label lanes is what the track row has room for. A lane is reused only
  // once the previous label in it is far enough away to leave a gap.
  const LANE_GAP_PCT = 9;
  const laneLastPct = [
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
  ];

  return scenes.map((scene, i) => {
    const pct = timeFraction(detail.off.t, detail.on.t, scene) * 100;

    let lane = laneLastPct.findIndex((last) => pct - last >= LANE_GAP_PCT);
    if (lane === -1) lane = laneLastPct.indexOf(Math.min(...laneLastPct));
    laneLastPct[lane] = pct;

    const element = h("span", { class: "radar-marker" });
    element.style.left = `${pct}%`;
    element.style.setProperty("--lane-offset", `${lane * 14}px`);
    // Keep a label at either end from hanging outside the timeline.
    element.style.setProperty(
      "--label-anchor",
      pct < 6 ? "0%" : pct > 94 ? "-100%" : "-50%",
    );
    element.append(
      h("span", { class: "radar-label" }, `radar pass ${i + 1}`),
      h("span", { class: "radar-leader" }),
      h("span", { class: "radar-pass" }),
    );
    return { element, candidates: byScene.get(scene) ?? [] };
  });
}

function tickRow(detail: CaseDetail): HTMLElement {
  const row = h("div", null);
  row.style.position = "relative";
  row.style.height = "16px";

  const hours = detail.hours;
  const step = tickStep(hours);
  for (let hr = step; hr <= hours; hr += step) {
    const pct = hours > 0 ? (hr / hours) * 100 : 0;
    const tick = h("span", { class: "tick" }, `+${hr} h`);
    tick.style.position = "absolute";
    tick.style.left = `${pct}%`;
    tick.style.transform = "translateX(-50%)";
    row.append(tick);
  }

  return row;
}

export function timeline(
  detail: CaseDetail,
  handlers: { onFrame(index: number): void },
): TimelineHandle {
  const frames = detail.lens_frames;
  const lastIndex = Math.max(0, frames.length - 1);
  let index = 0;
  let timer: number | null = null;

  const readout = h("div", { class: "time-readout" });

  const captionRow = h("div", null, h("span", { class: "sub" }, CAPTION), readout);
  captionRow.style.display = "flex";
  captionRow.style.alignItems = "baseline";
  captionRow.style.justifyContent = "space-between";
  captionRow.style.gap = "var(--sp-4)";

  const fill = h("div", { class: "fill" });

  const input = h("input", {
    class: "scrubber",
    type: "range",
    min: 0,
    max: lastIndex,
    step: 1,
    "aria-label": "Gap time",
  });
  input.value = "0";
  input.style.position = "absolute";
  input.style.inset = "0";
  input.style.width = "100%";

  const scrubber = h("div", { class: "scrubber" }, fill, input);
  scrubber.style.flex = "1 1 auto";
  scrubber.style.minWidth = "0";

  const markers = radarMarkers(detail, frames);
  for (const marker of markers) scrubber.append(marker.element);

  const playButton = h("button", { class: "btn", type: "button" }, "Play");
  const reducedMotion =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reducedMotion) {
    playButton.disabled = true;
    playButton.title = "Motion is off (reduced-motion setting)";
  }

  const trackRow = h("div", null, playButton, scrubber);
  trackRow.style.display = "flex";
  trackRow.style.alignItems = "center";
  trackRow.style.gap = "var(--sp-3)";

  const element = h("section", { class: "timeline" }, captionRow, trackRow, tickRow(detail));

  function update(notify: boolean): void {
    const pct = lastIndex > 0 ? (index / lastIndex) * 100 : 0;
    fill.style.width = `${pct}%`;
    input.value = String(index);

    const frame = frames[index];
    const t = frame ? frame.t : detail.off.t;
    readout.textContent = `t = ${fmtUtc(t)} ${fmtOffset(detail.off.t, t)}`;

    for (const marker of markers) {
      const live = marker.candidates.some((c) => isCandidateLive(frames, index, c));
      marker.element.classList.toggle("is-now", live);
    }

    if (notify) handlers.onFrame(index);
  }

  function stop(): void {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    playButton.textContent = "Play";
  }

  if (!reducedMotion) {
    playButton.addEventListener("click", () => {
      if (timer !== null) {
        stop();
        return;
      }
      playButton.textContent = "Pause";
      timer = window.setInterval(() => {
        index = lastIndex > 0 && index < lastIndex ? index + 1 : 0;
        update(true);
      }, 400);
    });
  }

  input.addEventListener("input", () => {
    const next = Number(input.value);
    index = Number.isFinite(next) ? clamp(Math.round(next), 0, lastIndex) : 0;
    update(true);
  });

  function setFrame(next: number): void {
    const value = Number(next);
    index = Number.isFinite(value) ? clamp(Math.round(value), 0, lastIndex) : 0;
    update(false);
  }

  update(false);

  return {
    element,
    setFrame,
    destroy(): void {
      stop();
    },
  };
}
