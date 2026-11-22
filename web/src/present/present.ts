/**
 * "Follow one ship" launcher (#274).
 *
 * The Present button (TopBar, delegated click) and the P key open `#/story`, the self-paced
 * demo of one real ship. The story owns its own keys (arrows, A, Escape). P toggles: on the
 * story it returns to the route that was on screen before; anywhere else it opens the story.
 */

import { PRESENT_ROUTES } from "./steps";

const STORY = PRESENT_ROUTES.story;
/** The route to return to when P is pressed on the story; the watch view when none was saved. */
const FALLBACK_RETURN = PRESENT_ROUTES.watch;

let returnHash = "";

/** Open the story, remembering where the presenter came from. */
export function startPresent(): void {
  if (location.hash === STORY) return;
  returnHash = location.hash;
  location.hash = STORY;
}

/** Leave the story for the route it was opened from (or the watch view). */
export function exitPresent(): void {
  if (location.hash !== STORY) return;
  const target = returnHash !== "" && returnHash !== STORY ? returnHash : FALLBACK_RETURN;
  returnHash = "";
  location.hash = target;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (isTyping(event.target)) return;
  if (event.key !== "p" && event.key !== "P") return;
  if (event.repeat) return;
  event.preventDefault();
  if (location.hash === STORY) exitPresent();
  else startPresent();
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

/** Wire the P key and the Present button. Safe to call once at boot. */
export function initPresent(): void {
  if (initialized) return;
  initialized = true;
  // Capture so P wins over any focused control or map handler.
  window.addEventListener("keydown", onKeyDown, true);
  document.addEventListener("click", onClick);
}
