/**
 * Presentation mode (design/v2/04-present.png).
 *
 * A fixed overlay over the live app: one caption, a thin progress bar across the
 * top and the keyboard hints under the caption. The beats are data (steps.ts);
 * stepping sets the hash (the router owns the screens) and, for the Hormuz cards,
 * the shared card request (cardRequest.ts).
 *
 * Every number is fetched once when the tour starts and cached for the run, from
 * the same endpoint its screen reads, with the screen's rounding. A missing
 * endpoint leaves the beat's number-free fallback sentence.
 *
 * The overlay is pointer-events: none. Keys: -> / Space next, <- back, Esc exits
 * and restores the route that was on screen when presentation started, P toggles.
 */

import "./present.css";

import type { WatchAlert } from "../api";
import { fetchEdgeStatus, fetchWatch } from "../api";
import { h } from "../components/dom";
import { daysAfterStrikes, leadCluster, leadListed, type HormuzStories } from "../components/hormuzStories";
import { requestHormuzCard } from "./cardRequest";
import {
  PRESENT_BEATS,
  PRESENT_ROUTES,
  watchAlertRoute,
  type PresentCaption,
  type PresentScene,
  type PresentToken,
} from "./steps";

const TREND_URL = "/api/hormuz/trend";
const STORIES_URL = "/api/hormuz/stories";
/** Matches the CSS breakpoint: the tour is desktop-only. */
const PHONE_QUERY = "(max-width: 1000px)";

/** After the last beat the tour leaves on its own, so a finished demo never sits on a caption. */

const TOKEN = /\{([a-z_0-9]+)\}/g;
const INTEGER = new Intl.NumberFormat("en-US");

/** Every scene, flattened, with the beat it belongs to (for the progress bar). */
const ALL_SCENES: readonly { scene: PresentScene; beat: number }[] = PRESENT_BEATS.flatMap((beat, b) =>
  beat.scenes.map((scene) => ({ scene, beat: b })),
);

/** The scenes this run shows: ALL_SCENES minus any the data cannot support (see applyAvailability). */
let SCENES: readonly { scene: PresentScene; beat: number }[] = ALL_SCENES;

/** A missing key means "no number from the API"; the beat falls back to its sentence. */
type TokenValues = Partial<Record<PresentToken, string>>;

let tokenValues: TokenValues | null = null;
/** The alert the "strongest" beat focuses; null until the watch fetch answers. */
let strongestId: string | null = null;

let active = false;
let index = 0;
/** Reserved for a timed exit; unused while the last beat waits for the presenter. */
let endTimer: number | null = null;
/** Bumped on start/exit so a fetch from an earlier run cannot repaint this one. */
let run = 0;
/** The hash that was on screen before presentation started; restored on exit. */
let startHash = "";

let root: HTMLElement | null = null;
let caption: HTMLElement | null = null;
let stepLabel: HTMLElement | null = null;
let headline: HTMLElement | null = null;
let line: HTMLElement | null = null;
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

/** A finite number or null; one guard for every optional field on the wire. */
function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
  return (await response.json()) as T;
}

/** The trend's regime block: the only fields the Hormuz beat prints. */
interface HormuzTrendLite {
  regime?: {
    treated?: { baseline?: { dark_share?: number | null }; current?: { dark_share?: number | null } };
    did?: number | null;
  };
}

/** Same rounding as the Hormuz headline: whole percents for the shares. */
function wholePercent(value: unknown): string | undefined {
  const fraction = finite(value);
  return fraction === null ? undefined : String(Math.round(fraction * 100));
}

function tokensFromTrend(trend: HormuzTrendLite): TokenValues {
  const regime = trend.regime;
  const tokens: TokenValues = {};
  const before = wholePercent(regime?.treated?.baseline?.dark_share);
  const after = wholePercent(regime?.treated?.current?.dark_share);
  const did = finite(regime?.did);
  if (before !== undefined) tokens.hormuz_before = before;
  if (after !== undefined) tokens.hormuz_after = after;
  // The Hormuz screen prints the difference with one decimal, in points.
  if (did !== null) tokens.did_pts = (did * 100).toFixed(1);
  return tokens;
}

function tokensFromStories(stories: HormuzStories): TokenValues {
  const tokens: TokenValues = {};
  const story = leadListed(stories);
  if (story !== null) {
    // Only a ship the data tags as shadow fleet may be called one; the rest are "on a list" or unnamed.
    tokens.sea1_kind = story.lists.includes("mare.shadow")
      ? "A shadow-fleet ship"
      : story.lists.length > 0
        ? "A ship on a sanctions list"
        : "A ship";
    tokens.sea1_hours = story.hours.toFixed(1);
    tokens.sea1_days = String(daysAfterStrikes(story.start));
    if (story.moved_km !== null) tokens.sea1_km = String(Math.round(story.moved_km));
  }
  const cluster = leadCluster(stories);
  if (cluster !== null) {
    tokens.cluster_ships = String(cluster.vessels);
    tokens.cluster_hours = cluster.span_hours.toFixed(1);
    if (cluster.median_kn !== null) tokens.cluster_kn = String(Math.round(cluster.median_kn));
  }
  return tokens;
}

/**
 * The alert the live beat focuses. Strongest first: an ongoing dark ship with the
 * most ships it could have met (the beat is about a ship switching off), then any
 * ongoing alert (the watch list leads with it), then the cold open's own order:
 * the newest earlier dark alert a sanctioned ship could have met, the newest
 * sanctioned stop, the newest faked position.
 */
function strongestAlert(alerts: readonly WatchAlert[]): WatchAlert | null {
  const met = (alert: WatchAlert): number => alert.contacts.filter((contact) => contact.could_meet === true).length;
  const newest = [...alerts].sort((a, b) => Date.parse(b.t_start) - Date.parse(a.t_start));
  const ongoing = newest.filter((alert) => alert.status === "ongoing");
  const liveDark = ongoing.filter((alert) => alert.kind === "dark").sort((a, b) => met(b) - met(a));
  if (liveDark[0] !== undefined) return liveDark[0];
  if (ongoing[0] !== undefined) return ongoing[0];
  return (
    newest.find(
      (alert) =>
        alert.kind === "dark" &&
        alert.contacts.some((contact) => contact.could_meet === true && contact.watchlist === "sanctioned"),
    ) ??
    newest.find((alert) => alert.kind === "sanctioned_stop") ??
    newest.find((alert) => alert.kind === "spoof") ??
    null
  );
}

/**
 * Fetch every number the captions may print, once per run. The calls are
 * independent (`allSettled`), so one dead endpoint only removes its own numbers.
 */
async function loadTourValues(): Promise<{
  tokens: TokenValues;
  strongest: string | null;
  absent: readonly PresentToken[];
}> {
  const [watch, trend, stories, edge] = await Promise.allSettled([
    fetchWatch(),
    getJson<HormuzTrendLite>(TREND_URL),
    getJson<HormuzStories>(STORIES_URL),
    fetchEdgeStatus(),
  ]);

  const tokens: TokenValues = {};
  let strongest: string | null = null;

  if (watch.status === "fulfilled") {
    const alerts = Array.isArray(watch.value.alerts) ? watch.value.alerts : [];
    const focus = strongestAlert(alerts);
    if (focus !== null) {
      strongest = focus.id;
      // Only a plural count reads well ("the 8 ships"); 0 or 1 falls back to the line without it.
      const metCount = focus.contacts.filter((contact) => contact.could_meet === true).length;
      if (metCount >= 2) tokens.met = String(metCount);
    }
  }

  if (trend.status === "fulfilled") Object.assign(tokens, tokensFromTrend(trend.value));
  // A stories answer with no listed ship means the SEA 1 scene has nothing to tell.
  const absent: PresentToken[] = [];
  if (stories.status === "fulfilled") {
    Object.assign(tokens, tokensFromStories(stories.value));
    if (leadListed(stories.value) === null) absent.push("sea1_hours");
  }

  if (edge.status === "fulfilled") {
    const ms = finite(edge.value.last_recovery_ms);
    if (ms !== null && ms >= 0) tokens.recovery = `${Math.round(ms)} ms`;
  }

  return { tokens, strongest, absent };
}

/**
 * Drop the scenes whose required token the API said is absent, and keep the viewer
 * on the same scene (or the one that took its place) so the tour does not jump.
 */
function applyAvailability(absent: readonly PresentToken[]): void {
  if (absent.length === 0) return;
  const current = SCENES[index]?.scene;
  const previous = index;
  SCENES = ALL_SCENES.filter((entry) => entry.scene.requires === undefined || !absent.includes(entry.scene.requires));
  const same = SCENES.findIndex((entry) => entry.scene === current);
  index = same >= 0 ? same : Math.min(previous, SCENES.length - 1);
}

function ensureTourValues(myRun: number): void {
  void loadTourValues()
    .then((loaded) => {
      if (myRun !== run) return;
      tokenValues = loaded.tokens;
      strongestId = loaded.strongest;
      applyAvailability(loaded.absent);
      // Re-show the same scene: its route may now name the strongest alert.
      show(index);
    })
    .catch(() => {
      // Keep the fallbacks; a failed fetch must never put "undefined" on screen.
    });
}

/**
 * Replace every `{token}` with its live value. Null when any token is unknown
 * (including before the fetch answers), so the caller can use the fallback.
 */
function fill(template: string, values: TokenValues | null): string | null {
  let missing = false;
  const text = template.replace(TOKEN, (_match, name: string) => {
    const value = values?.[name as PresentToken];
    if (value === undefined) {
      missing = true;
      return "";
    }
    return value;
  });
  return missing ? null : text;
}

/** Last-resort safety: never show a raw placeholder if a scene forgot its fallback. */
function stripTokens(template: string): string {
  return template
    .replace(TOKEN, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;:])/g, "$1")
    .trim();
}

function captionFor(scene: PresentScene): PresentCaption {
  return {
    headline: fill(scene.headline, tokenValues) ?? scene.fallback?.headline ?? stripTokens(scene.headline),
    line: fill(scene.line, tokenValues) ?? scene.fallback?.line ?? stripTokens(scene.line),
  };
}

/** The route for a scene, or the world view while the strongest alert is unknown. */
function routeFor(scene: PresentScene): string {
  if (scene.alert === "strongest") {
    return strongestId === null ? PRESENT_ROUTES.watch : watchAlertRoute(strongestId);
  }
  return PRESENT_ROUTES[scene.route];
}

/** Swap the caption to one scene and replay its 200 ms fade-in. */
function show(at: number): void {
  const entry = SCENES[at];
  if (entry === undefined || root === null) return;

  const { scene, beat } = entry;
  const text = captionFor(scene);
  index = at;
  clearEndTimer();
  // No auto-exit: the presenter may still be speaking on the last beat; → or Esc leaves.
  navigate(routeFor(scene));
  requestHormuzCard(scene.card ?? null);

  if (stepLabel !== null) stepLabel.textContent = `${beat + 1} / ${PRESENT_BEATS.length}`;
  if (headline !== null) headline.textContent = text.headline;
  if (line !== null) line.textContent = text.line;
  if (caption !== null) caption.dataset["dock"] = scene.dock ?? "bottom";
  if (progressFill !== null) progressFill.style.width = `${((beat + 1) / PRESENT_BEATS.length) * 100}%`;

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

function clearEndTimer(): void {
  if (endTimer === null) return;
  window.clearTimeout(endTimer);
  endTimer = null;
}

function buildOverlay(): HTMLElement {
  progressFill = h("i", { class: "present-progress-fill" });
  const progress = h(
    "div",
    {
      class: "present-progress",
      role: "progressbar",
      "aria-label": "Presentation progress",
      "aria-valuemin": 1,
      "aria-valuemax": PRESENT_BEATS.length,
      "aria-valuenow": 1,
    },
    progressFill,
  );

  stepLabel = h("span", { class: "present-step" });
  headline = h("h1", { class: "present-headline" });
  line = h("p", { class: "present-line" });

  // The keyboard hints live inside the caption so they move with it: a docked
  // caption must not leave them over the status bar.
  const hints = h(
    "div",
    { class: "present-hints" },
    hint("→", "next"),
    " · ",
    hint("←", "back"),
    " · ",
    hint("Esc", "exit"),
  );

  caption = h(
    "div",
    { class: "present-caption", role: "status", "aria-live": "polite" },
    h("div", { class: "present-meta" }, stepLabel),
    headline,
    line,
    hints,
  );

  return h(
    "div",
    { class: "present-root", role: "region", "aria-label": "Presentation mode" },
    progress,
    h("div", { class: "present-scrim" }),
    caption,
  );
}

function tourUnsupported(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(PHONE_QUERY).matches;
}

/** Start at the first scene. Calling it while active restarts from the top. */
export function startPresent(): void {
  // The tour needs the width of a laptop: on a phone the overlay is not drawn.
  if (tourUnsupported()) return;

  if (!active) {
    startHash = location.hash;
    // Each run gets fresh numbers; they are cached until the run ends.
    run += 1;
    tokenValues = null;
    strongestId = null;
    SCENES = ALL_SCENES;
    index = 0;
    root = buildOverlay();
    document.body.append(root);
    document.body.classList.add("is-presenting");
    active = true;
    ensureTourValues(run);
  }
  show(0);
}

/** Leave presentation and put the original route back. */
export function exitPresent(): void {
  if (!active) return;
  active = false;
  run += 1; // invalidate a fetch that is still in flight
  clearEndTimer();
  requestHormuzCard(null);
  document.body.classList.remove("is-presenting");
  root?.remove();
  root = null;
  caption = null;
  stepLabel = null;
  headline = null;
  line = null;
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
    // Next after the last beat leaves the tour, back to the app.
    if (index >= SCENES.length - 1) exitPresent();
    else show(index + 1);
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
  // A window narrowed to phone width drops the tour rather than leaving an
  // invisible overlay owning Escape.
  window.addEventListener("resize", () => {
    if (active && tourUnsupported()) exitPresent();
  });
}
