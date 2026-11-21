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

import { fetchCases, fetchLiveEvents, fetchLiveSummary, fetchLiveVessels, fetchStats } from "../api";
import { h } from "../components/dom";
import type { LiveEvent, LiveVessel, LiveWatchlistEntry } from "../model/live";
import { unusualCount, vesselName, watchlistAlerts, windowHours } from "../model/liveFacts";
import { PRESENT_STEPS, type PresentCaption, type PresentStep, type PresentToken } from "./steps";

const TOTAL = PRESENT_STEPS.length;
const BALTIC_REGION = "BALTIC_WATCH";
const HORMUZ_UNSEEN_URL = "/api/hormuz/unseen";
const TOKEN = /\{([a-z_]+)\}/g;
const INTEGER = new Intl.NumberFormat("en-US");
const EVENT_COUNT_KEYS = ["silences", "jumps", "clones", "meetings", "spoofs"] as const;

/**
 * Live values for the caption tokens. A missing key means "no number from the
 * API", and the step falls back to its number-free sentence. Null means the
 * fetch has not answered yet.
 */
type TokenValues = Partial<Record<PresentToken, string>>;
let tokenValues: TokenValues | null = null;
let loadingTokens = false;

/**
 * Values that belong in a route rather than a caption (today: the watchlist
 * vessel the camera should open). Kept apart so a missing route value falls
 * back to the beat's safer route instead of breaking the hash.
 */
let routeValues: Record<string, string> = {};

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

/** A finite number or null; one guard for every optional field on the wire. */
function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function integerToken(value: number | null): string | undefined {
  return value === null ? undefined : INTEGER.format(Math.round(value));
}

function ratioToken(value: number | null): string | undefined {
  return value === null ? undefined : value.toFixed(2);
}

/** The spoof event the "physics" beat is about: highest severity, as the API sorts. */
function topSpoof(events: LiveEvent[]): LiveEvent | null {
  let best: LiveEvent | null = null;
  for (const event of events) {
    if (event.kind !== "spoof") continue;
    if (best === null || event.severity > best.severity) best = event;
  }
  return best;
}

/**
 * The watchlist ALERT vessel the recording heard most recently. Alerts carry
 * the MMSI or IMO the list matched; the vessels carry the broadcast fixes, so
 * the pick needs both. A vessel with no last fix cannot be "seen recently" and
 * is skipped rather than guessed at.
 */
function latestAlertVessel(
  alerts: LiveWatchlistEntry[],
  vessels: LiveVessel[],
): { mmsi: number; name: string; at: number } | null {
  const byMmsi = new Map(vessels.map((vessel) => [vessel.mmsi, vessel]));
  const byImo = new Map<number, LiveVessel>();
  for (const vessel of vessels) {
    if (vessel.imo !== null) byImo.set(vessel.imo, vessel);
  }

  let best: { mmsi: number; name: string; at: number } | null = null;
  for (const alert of alerts) {
    const vessel =
      (alert.mmsi === null ? undefined : byMmsi.get(alert.mmsi)) ??
      (alert.imo === null ? undefined : byImo.get(alert.imo));
    if (vessel === undefined || vessel.last === null) continue;

    const at = Date.parse(vessel.last.t);
    if (!Number.isFinite(at)) continue;
    if (best === null || at > best.at) {
      best = { mmsi: vessel.mmsi, name: vesselName(vessel.mmsi, vessels), at };
    }
  }
  return best;
}

/** Fill `{route_token}` placeholders; null while any value is still unknown. */
function fillRoute(template: string): string | null {
  let missing = false;
  const route = template.replace(TOKEN, (_match, name: string) => {
    const value = routeValues[name];
    if (value === undefined) {
      missing = true;
      return "";
    }
    return encodeURIComponent(value);
  });
  return missing ? null : route;
}

/** The route for a beat, or its safer fallback while a route value is missing. */
function routeFor(beat: PresentStep): string {
  return fillRoute(beat.route) ?? beat.fallbackRoute ?? "#/";
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
  return (await response.json()) as T;
}

/** Hormuz's snapshot is local to its block; presentation needs only these fields. */
interface HormuzSummaryLite {
  days?: number;
  dark_share_mean?: number | null;
  headline_share?: number | null;
  n_days_used?: number;
}

interface HormuzUnseenLite {
  summary?: HormuzSummaryLite;
}

/**
 * Fetch every number the captions may print, once per presentation run. The
 * calls are independent (`allSettled`) so one dead endpoint only removes its
 * own numbers; each step then reads its fallback sentence.
 */
async function loadTokenValues(): Promise<TokenValues> {
  const [cases, stats, live, vessels, events, hormuz] = await Promise.allSettled([
    fetchCases({ limit: 200, status: "all" }),
    fetchStats(),
    fetchLiveSummary(BALTIC_REGION),
    fetchLiveVessels(BALTIC_REGION),
    fetchLiveEvents(BALTIC_REGION),
    getJson<HormuzUnseenLite>(HORMUZ_UNSEEN_URL),
  ]);

  const values: TokenValues = {};

  if (cases.status === "fulfilled") {
    const { totals, cases: rows } = cases.value;
    // candidates_final is optional on older snapshots; the listed cases are the fallback.
    const final = finite((totals as { candidates_final?: unknown }).candidates_final);
    const unexplained =
      final ?? (rows.length > 0 ? rows.filter((row) => row.status === "candidate").length : null);

    values.gaps_scanned = integerToken(finite(totals.gaps_scanned));
    values.radar_nearby = integerToken(finite(totals.with_candidates));
    values.cleared = integerToken(finite(totals.ruled_out));
    values.unexplained = integerToken(unexplained);
  }

  if (stats.status === "fulfilled") {
    const validation = stats.value.validation;
    values.null_ratio = ratioToken(finite(validation?.null_ratio));
    values.bridge_auc = ratioToken(finite(validation?.bridge_auc));
  }

  if (live.status === "fulfilled") {
    const counts = live.value.counts ?? {};
    values.baltic_ships = integerToken(finite(counts.vessels));

    const alerts = watchlistAlerts(live.value.watchlist);
    values.alerts_at_least = integerToken(alerts.length);

    // "seen in the last N h" is the only honest wording for a recording, so
    // the window comes from the same summary as the alerts.
    const hours = windowHours(live.value.window);
    if (Number.isFinite(hours) && hours > 0) values.window_h = String(Math.round(hours));

    if (vessels.status === "fulfilled") {
      const picked = latestAlertVessel(alerts, vessels.value);
      if (picked !== null) {
        routeValues.watchlist_mmsi = String(picked.mmsi);
        values.watchlist_vessel = picked.name;
      }
    }

    const hasEventCounts = EVENT_COUNT_KEYS.some((key) => finite(counts[key]) !== null);
    if (hasEventCounts) {
      values.baltic_events = integerToken(unusualCount(counts));
    }
  }

  if (events.status === "fulfilled") {
    const spoof = topSpoof(events.value);
    if (spoof !== null) {
      const listed = Array.isArray(spoof.evidence["vessels"]) ? spoof.evidence["vessels"].length : null;
      values.spoof_ships = integerToken(finite(spoof.evidence["n_vessels"]) ?? listed);
    }
  }

  if (hormuz.status === "fulfilled") {
    const summary = hormuz.value.summary;
    const share = finite(summary?.headline_share) ?? finite(summary?.dark_share_mean);
    if (share !== null) values.hormuz_share_pct = String(Math.round(share * 100));
    values.hormuz_days_used = integerToken(finite(summary?.n_days_used) ?? finite(summary?.days));
  }

  return values;
}

function ensureTokenValues(): void {
  if (loadingTokens) return;
  loadingTokens = true;
  void loadTokenValues()
    .then((values) => {
      tokenValues = values;
      // Repaint the beat the presenter is on; navigation is a no-op for the same route.
      if (active) show(index);
    })
    .catch(() => {
      // Keep the fallbacks; a failed fetch must never put "undefined" on screen.
    })
    .finally(() => {
      loadingTokens = false;
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

/** Last-resort safety: never show a raw placeholder if a step forgot its fallback. */
function stripTokens(template: string): string {
  return template
    .replace(TOKEN, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;:])/g, "$1")
    .trim();
}

function captionFor(beat: PresentStep): PresentCaption {
  return {
    headline: fill(beat.headline, tokenValues) ?? beat.fallback?.headline ?? stripTokens(beat.headline),
    line: fill(beat.line, tokenValues) ?? beat.fallback?.line ?? stripTokens(beat.line),
    proof: fill(beat.proof, tokenValues) ?? beat.fallback?.proof ?? stripTokens(beat.proof),
  };
}

/** Swap the caption to one step and replay its 200 ms fade-in. */
function show(step: number): void {
  const beat = PRESENT_STEPS[step];
  if (beat === undefined || root === null) return;

  const text = captionFor(beat);
  index = step;
  navigate(routeFor(beat));

  if (stepLabel !== null) stepLabel.textContent = `${step + 1} / ${TOTAL}`;
  if (proofLabel !== null) proofLabel.textContent = text.proof;
  if (headline !== null) headline.textContent = text.headline;
  if (line !== null) line.textContent = text.line;
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
    // The recording grows between runs: fetch a fresh set of numbers, once per run.
    tokenValues = null;
    routeValues = {};
    root = buildOverlay();
    document.body.append(root);
    document.body.classList.add("is-presenting");
    active = true;
    ensureTokenValues();
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
