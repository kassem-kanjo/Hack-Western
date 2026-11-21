/**
 * Replay controls for the Live screen's time strip (#85).
 *
 * V1 of time travel: the strip's axis becomes a scrubber over the recorded
 * window, and this cluster carries the two things a scrubber cannot say by
 * itself — whether the clock is running, and how fast. The component owns no
 * state: the screen keeps `t` and the clock and passes both back in, so the
 * strip, the map, the hero number and the rail can never disagree.
 *
 * Speed is measured against the window, not against the wall clock: the window
 * is the unit being played. 60x is the reference the design fixed at ~20 s for
 * the whole recording; 10x and 1x stretch from there, so a demo can dwell on a
 * stretch without inventing a second timeline.
 */

import { h } from "./dom";

export type ReplaySpeed = 1 | 10 | 60;

/** Cheapest first: the button cycles through these, then wraps. */
export const REPLAY_SPEEDS: readonly ReplaySpeed[] = [1, 10, 60];

/** How long the whole window takes at 60x. Every other speed scales from this. */
export const REPLAY_WINDOW_MS_AT_60X = 20_000;

/** Wall-clock milliseconds the whole window takes at one speed. */
export function replayDurationMs(speed: ReplaySpeed): number {
  return (REPLAY_WINDOW_MS_AT_60X * 60) / speed;
}

/** "20 s", "2 min": what one pass of the window costs at this speed. */
function durationLabel(speed: ReplaySpeed): string {
  const seconds = replayDurationMs(speed) / 1000;
  return seconds < 60 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
}

export interface ReplayBarOptions {
  /** Play/pause pressed. The screen owns the clock, so it decides what that means. */
  onTogglePlay(): void;
  /** The speed chip was pressed; the screen cycles to the next speed. */
  onCycleSpeed(): void;
  /** A drag or key interaction started: the screen holds the clock until `onScrubEnd`. */
  onScrubStart(): void;
  /** A new position as a fraction of the window in [0, 1]. */
  onScrub(fraction: number): void;
  onScrubEnd(): void;
}

export interface ReplayBarHandle {
  /** Play/pause and speed, mounted beside the Replay button in the strip. */
  element: HTMLElement;
  /** The range input itself; the screen lays it over the axis track. */
  slider: HTMLInputElement;
  setPlaying(playing: boolean): void;
  setSpeed(speed: ReplaySpeed): void;
  /** Move the thumb without firing `onScrub` (the screen is pushing its own time in). */
  setFraction(fraction: number): void;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** Play triangle or pause bars, drawn inline: no icon font and no idle motion. */
function transportIcon(playing: boolean): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", "12");
  svg.setAttribute("height", "12");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "live-rb-icon");

  if (playing) {
    for (const x of [2.5, 7]) {
      const bar = document.createElementNS(SVG_NS, "rect");
      bar.setAttribute("x", String(x));
      bar.setAttribute("y", "1.5");
      bar.setAttribute("width", "2");
      bar.setAttribute("height", "9");
      bar.setAttribute("fill", "currentColor");
      svg.append(bar);
    }
  } else {
    const triangle = document.createElementNS(SVG_NS, "path");
    triangle.setAttribute("d", "M2.5 1.5 L10 6 L2.5 10.5 Z");
    triangle.setAttribute("fill", "currentColor");
    svg.append(triangle);
  }

  return svg;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

export function replayBar(opts: ReplayBarOptions): ReplayBarHandle {
  const play = h("button", {
    class: "live-rb-btn live-rb-play",
    type: "button",
    "aria-label": "Play replay",
    title: "Play / pause (space)",
  });
  const speed = h(
    "button",
    {
      class: "live-rb-btn live-rb-speed",
      type: "button",
      title: `Playback speed (1×, 10×, 60×) — whole window in ${durationLabel(60)} at 60×`,
      "aria-label": "Playback speed 60×",
    },
    "60×",
  );

  // A native range keeps dragging, touch and the keyboard for free. It is laid
  // over the tick strip by the screen; 0..1000 is fine enough for a 24 h window
  // (about 1.4 min per step) and keeps the value arithmetic exact enough.
  const slider = h("input", {
    class: "live-scrub",
    type: "range",
    min: 0,
    max: 1000,
    step: 1,
    value: 0,
    "aria-label": "Replay position",
    "aria-valuetext": "0% of the window",
  });

  let playing = false;

  function paintTransport(): void {
    play.setAttribute("aria-label", playing ? "Pause replay" : "Play replay");
    play.replaceChildren(transportIcon(playing));
  }

  play.addEventListener("click", () => opts.onTogglePlay());
  speed.addEventListener("click", () => opts.onCycleSpeed());

  slider.addEventListener("input", () => {
    const value = Number(slider.value);
    opts.onScrub(clamp01(Number.isFinite(value) ? value / 1000 : 0));
  });
  // The clock must not fight the thumb; hold it for the whole drag.
  slider.addEventListener("pointerdown", () => opts.onScrubStart());
  slider.addEventListener("pointerup", () => opts.onScrubEnd());
  slider.addEventListener("pointercancel", () => opts.onScrubEnd());
  // A keyboard-driven change has no pointer to release it.
  slider.addEventListener("change", () => opts.onScrubEnd());

  paintTransport();

  const element = h("div", { class: "live-rb" }, play, speed);

  return {
    element,
    slider,

    setPlaying(next: boolean): void {
      if (playing === next) return;
      playing = next;
      paintTransport();
    },

    setSpeed(next: ReplaySpeed): void {
      speed.textContent = `${next}×`;
      speed.setAttribute("aria-label", `Playback speed ${next}×`);
      speed.title = `Playback speed (1×, 10×, 60×) — whole window in ${durationLabel(next)} at ${next}×`;
    },

    setFraction(fraction: number): void {
      const value = clamp01(fraction);
      slider.value = String(Math.round(value * 1000));
      slider.setAttribute("aria-valuetext", `${Math.round(value * 100)}% of the window`);
    },
  };
}
