/**
 * First-visit intro card (#86).
 *
 * One centred card over a dimmed page, shown once per browser before the
 * console is explored. It answers "what is this?" and offers exactly two ways
 * in: the guided walkthrough (the same startPresent() the Present button calls)
 * or the console itself.
 *
 * Shown only on a fresh visit to the root route: `?nointro` and any deep link
 * (`#/live/...`, `#/case/...`, ...) skip it, so a shared judge link lands on
 * its event. Every close path writes the seen key; storage failures (private
 * mode) are swallowed so the card still works, it just returns next visit.
 */

import "../styles/intro.css";

import { startPresent } from "../present/present";
import { h } from "./dom";

const SEEN_KEY = "df.intro.seen";
const FADE_MS = 150;

const TITLE = "Ships that hide, checked with physics";
const LINE_1 =
  "Ships can switch off their location broadcast. Radar satellites and our own receivers still see a lot of them.";
const LINE_2 =
  "Dark Fleet checks every gap and every jump against what a ship can physically do, and says why each alert is an alert.";
const LINE_3 = "Nothing here accuses a ship on radar alone.";

const TOUR = "Take the 2-minute tour";
const EXPLORE = "Explore on my own";

let backdrop: HTMLElement | null = null;
let card: HTMLElement | null = null;
let tourButton: HTMLButtonElement | null = null;
let exploreButton: HTMLButtonElement | null = null;
let previousFocus: HTMLElement | null = null;

/** False in browsers that refuse storage (Safari private mode); then show the card. */
function alreadySeen(): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) !== null;
  } catch {
    return false;
  }
}

function rememberSeen(): void {
  try {
    window.localStorage.setItem(SEEN_KEY, "1");
  } catch {
    // Storage is unavailable; the card simply shows again on the next visit.
  }
}

/** A deep link (or ?nointro) owns the first paint; the card must not cover it. */
function introAllowed(): boolean {
  if (location.hash !== "" && location.hash !== "#/") return false;
  return !new URLSearchParams(location.search).has("nointro");
}

function isVisible(el: HTMLElement): boolean {
  return el.getClientRects().length > 0 && window.getComputedStyle(el).visibility !== "hidden";
}

/** Tab order = the buttons the current breakpoint actually shows (the tour hides on phones). */
function focusables(): HTMLButtonElement[] {
  return [tourButton, exploreButton].filter(
    (button): button is HTMLButtonElement => button !== null && isVisible(button),
  );
}

function focusPrimary(): void {
  const [first] = focusables();
  first?.focus();
}

function onFocusIn(event: FocusEvent): void {
  if (card === null) return;
  if (event.target instanceof Node && card.contains(event.target)) return;
  focusPrimary();
}

function onKeyDown(event: KeyboardEvent): void {
  if (backdrop === null) return;

  // Leave browser chords (Cmd+R, Ctrl+L, ...) alone.
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  if (event.key === "Escape") {
    event.preventDefault();
    event.stopImmediatePropagation();
    close();
    return;
  }

  if (event.key === "Tab") {
    event.preventDefault();
    event.stopImmediatePropagation();
    const items = focusables();
    if (items.length === 0) return;

    const current = items.findIndex((item) => item === document.activeElement);
    const next =
      current === -1
        ? event.shiftKey
          ? items.length - 1
          : 0
        : (current + (event.shiftKey ? -1 : 1) + items.length) % items.length;
    items[next]?.focus();
    return;
  }

  // The page behind the card is modal: "P" must not start a walkthrough under it.
  event.stopImmediatePropagation();
}

function close(): void {
  const node = backdrop;
  if (node === null) return;

  rememberSeen();

  backdrop = null;
  card = null;
  tourButton = null;
  exploreButton = null;

  window.removeEventListener("keydown", onKeyDown, true);
  document.removeEventListener("focusin", onFocusIn, true);

  node.classList.remove("is-in");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.setTimeout(() => node.remove(), reduced ? 0 : FADE_MS);

  // There is no trigger element on a first visit: don't strand focus on a
  // button that is fading out.
  const previous = previousFocus;
  previousFocus = null;
  if (previous !== null && previous !== document.body && previous.isConnected) {
    previous.focus();
  } else if (document.activeElement instanceof HTMLElement) {
    document.activeElement.blur();
  }
}

function open(): void {
  if (backdrop !== null) return;
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  tourButton = h(
    "button",
    { class: "intro-btn intro-btn--primary intro-tour", type: "button" },
    TOUR,
  );
  exploreButton = h(
    "button",
    { class: "intro-btn intro-explore", type: "button" },
    EXPLORE,
  );

  const title = h("h2", { class: "intro-title", id: "intro-title" }, TITLE);
  const copy = h(
    "div",
    { class: "intro-copy", id: "intro-copy" },
    h("p", null, LINE_1),
    h("p", null, LINE_2),
    h("p", null, LINE_3),
  );

  const panel = h(
    "div",
    {
      class: "intro-card",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "intro-title",
      "aria-describedby": "intro-copy",
    },
    title,
    copy,
    h("div", { class: "intro-actions" }, tourButton, exploreButton),
  );

  const node = h("div", { class: "intro-backdrop" }, panel);
  node.addEventListener("click", (event) => {
    if (event.target === node) close();
  });
  tourButton.addEventListener("click", () => {
    close();
    startPresent();
  });
  exploreButton.addEventListener("click", () => {
    close();
  });

  backdrop = node;
  card = panel;
  document.body.append(node);

  // Read layout once so the fade runs from transparent to opaque.
  void node.offsetWidth;
  node.classList.add("is-in");

  // Capture on window, before present.ts's global handler (initIntro runs
  // first in main.ts): Escape closes here and "P" stays inside the modal.
  window.addEventListener("keydown", onKeyDown, true);
  document.addEventListener("focusin", onFocusIn, true);
  focusPrimary();
}

/**
 * True when the card will show on this page load (fresh root visit, not seen yet). The top-bar
 * hint asks this so a visitor never sees both the card and the hint. Storage-based, so it
 * answers the same before and after the card is dismissed.
 */
export function introWillShow(): boolean {
  return introAllowed() && !alreadySeen();
}

/** Show the card once, when this is a fresh root visit and storage has no key. */
export function initIntro(): void {
  if (!introAllowed()) return;
  if (alreadySeen()) return;
  open();
}
