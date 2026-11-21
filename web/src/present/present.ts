/**
 * Presentation mode (UI V2, design/v2/04-present.png).
 *
 * A fixed overlay over the live app: one caption bottom-left, a thin progress
 * bar across the top and the keyboard hints bottom-right. Steps are data (see
 * steps.ts); stepping only assigns location.hash, so the router keeps owning
 * the screens and the view behind the caption is the real one.
 *
 * The overlay itself is pointer-events: none: only the app's own controls and
 * the topbar stay clickable. Keys: -> / Space next, <- back, Esc exits and
 * restores the route that was on screen when presentation started, P toggles.
 */

import "./present.css";

import { h } from "../components/dom";
import { PRESENT_STEPS } from "./steps";

const TOTAL = PRESENT_STEPS.length;

let active = false;
let index = 0;
/** The hash that was on screen before presentation started; restored on exit. */
let startHash = "";

let root: HTMLElement | null = null;
let caption: HTMLElement | null = null;
let stepLabel: HTMLElement | null = null;
let proofLabel: HTMLElement | null = null;
let headline: HTMLElement | null = null;
let line: HTMLElement | null = null;
let progress: HTMLElement | null = null;
let progressFill: HTMLElement | null = null;

/** Hash navigation is the router's contract; never touch the screen directly. */
function navigate(route: string): void {
  if (location.hash === route) return;
  location.hash = route;
}

/**
 * Put the starting route back. An empty hash is a real route ("#/"), and
 * assigning "" leaves a bare "#" behind, so remove it with replaceState and
 * ask the router to repaint once.
 */
function restore(hash: string): void {
  if (location.hash === hash) return;
  if (hash === "") {
    history.replaceState(null, "", location.pathname + location.search);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    return;
  }
  location.hash = hash;
}

function setProgress(step: number): void {
  if (progress !== null) {
    progress.setAttribute("aria-valuenow", String(step + 1));
    progress.setAttribute("aria-valuetext", `Step ${step + 1} of ${TOTAL}`);
  }
  if (progressFill !== null) {
    progressFill.style.width = `${((step + 1) / TOTAL) * 100}%`;
  }
}

/** Swap the caption to one step and replay its 200 ms fade-in. */
function show(step: number): void {
  const beat = PRESENT_STEPS[step];
  if (beat === undefined || root === null) return;

  index = step;
  navigate(beat.route);

  if (stepLabel !== null) stepLabel.textContent = `${step + 1} / ${TOTAL}`;
  if (proofLabel !== null) proofLabel.textContent = beat.proof;
  if (headline !== null) headline.textContent = beat.headline;
  if (line !== null) line.textContent = beat.line;
  setProgress(step);

  // Removing and re-adding in the same frame restarts the CSS animation; the
  // read of offsetWidth flushes the style change between the two.
  if (caption !== null) {
    caption.classList.remove("is-in");
    void caption.offsetWidth;
    caption.classList.add("is-in");
  }
}

function hint(key: string, label: string): HTMLElement {
  return h("span", { class: "present-hint" }, h("kbd", { class: "present-kbd" }, key), ` ${label}`);
}

function buildOverlay(): HTMLElement {
  progressFill = h("i", { class: "present-progress-fill" });
  progress = h(
    "div",
    {
      class: "present-progress",
      role: "progressbar",
      "aria-label": "Presentation progress",
      "aria-valuemin": 1,
      "aria-valuemax": TOTAL,
      "aria-valuenow": 1,
      "aria-valuetext": `Step 1 of ${TOTAL}`,
    },
    progressFill,
  );

  stepLabel = h("span", { class: "present-step" });
  proofLabel = h("span", { class: "present-proof" });
  headline = h("h1", { class: "present-headline" });
  line = h("p", { class: "present-line" });
  caption = h(
    "div",
    { class: "present-caption", role: "status", "aria-live": "polite" },
    h("div", { class: "present-meta" }, stepLabel, proofLabel),
    headline,
    line,
  );

  const hints = h(
    "div",
    { class: "present-hints" },
    hint("→", "next"),
    " · ",
    hint("←", "back"),
    " · ",
    hint("Esc", "exit"),
  );

  return h(
    "div",
    { class: "present-root", role: "region", "aria-label": "Presentation mode" },
    progress,
    h("div", { class: "present-scrim" }),
    caption,
    hints,
  );
}

/** Start at step 1. Calling it while active restarts from the first beat. */
export function startPresent(): void {
  if (!active) {
    startHash = location.hash;
    root = buildOverlay();
    document.body.append(root);
    document.body.classList.add("is-presenting");
    active = true;
  }
  show(0);
}

/** Leave presentation and put the original route back. */
export function exitPresent(): void {
  if (!active) return;
  active = false;
  document.body.classList.remove("is-presenting");
  root?.remove();
  root = null;
  caption = null;
  stepLabel = null;
  proofLabel = null;
  headline = null;
  line = null;
  progress = null;
  progressFill = null;
  restore(startHash);
  startHash = "";
}

function togglePresent(): void {
  if (active) exitPresent();
  else startPresent();
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (isTyping(event.target)) return;

  if (event.key === "p" || event.key === "P") {
    if (event.repeat) return;
    event.preventDefault();
    togglePresent();
    return;
  }

  if (!active) return;

  if (event.key === "ArrowRight" || event.key === " " || event.code === "Space") {
    event.preventDefault();
    if (event.repeat) return;
    show(Math.min(index + 1, TOTAL - 1));
    return;
  }

  if (event.key === "ArrowLeft") {
    event.preventDefault();
    if (event.repeat) return;
    show(Math.max(index - 1, 0));
    return;
  }

  if (event.key === "Escape") {
    event.preventDefault();
    exitPresent();
  }
}

/**
 * The topbar is rebuilt on every navigation, so the Present button is caught by
 * delegation instead of a listener that would go stale.
 */
function onClick(event: MouseEvent): void {
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (target.closest(".present-btn") === null) return;
  event.preventDefault();
  startPresent();
}

let initialized = false;

/** Wire the global keys and the Present button. Safe to call once at boot. */
export function initPresent(): void {
  if (initialized) return;
  initialized = true;
  // Capture so the presentation keys win over any focused control or map handler.
  window.addEventListener("keydown", onKeyDown, true);
  document.addEventListener("click", onClick);
}
