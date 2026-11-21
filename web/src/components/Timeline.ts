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

/** Marker label sits inside the rotated diamond, so counter-rotate the text. */
function radarLabel(n: number): HTMLElement {
  const label = h("span", { class: "radar-label" }, `radar pass ${n}`);
  label.style.position = "absolute";
  label.style.left = "50%";
  label.style.top = "14px";
  label.style.transform = "translateX(-50%) rotate(-45deg)";
  label.style.transformOrigin = "left top";
  label.style.whiteSpace = "nowrap";
  label.style.fontSize = "var(--fs-caption)";
  label.style.color = "var(--c-text-3)";
  return label;
}

/** One marker per distinct radar scene time, numbered in time order. */
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

  return scenes.map((scene, i) => {
    const pct = timeFraction(detail.off.t, detail.on.t, scene) * 100;
    const element = h("span", { class: "radar-pass" }, radarLabel(i + 1));
    element.style.position = "absolute";
    element.style.top = "-1px";
    element.style.left = `${pct}%`;
    return { element, candidates: byScene.get(scene) ?? [] };
  });
}

function tickRow(detail: CaseDetail): HTMLElement {
  const row = h("div", null);
  row.style.position = "relative";
  row.style.height = "16px";

  const hours = detail.hours;
  for (let hr = 4; hr <= hours; hr += 4) {
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
