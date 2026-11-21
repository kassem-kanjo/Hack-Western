/**
 * Presentation mode (design/v2/04-present.png).
 *
 * A fixed overlay over the live app: one bottom-centre caption, a thin progress
 * bar across the top and the keyboard hints under the caption. Steps are data
 * (see steps.ts); stepping only assigns location.hash, so the router keeps
 * owning the screens and the view behind the caption is the real one.
 *
 * Every number on every beat is fetched once when the tour starts and cached
 * for the whole run, so the radar share, the live counts and the spoof offsets
 * cannot disagree between screens. The Hormuz-trend beat only exists when
 * /api/hormuz/trend reports regime.verdict "moves with event"; otherwise it is not in the list.
 *
 * The overlay itself is pointer-events: none: only the app's own controls and
 * the topbar stay clickable. Keys: -> / Space next, <- back, Esc exits and
 * restores the route that was on screen when presentation started, P toggles.
 */

import "./present.css";

import type { WatchAlert } from "../api";
import { fetchEdgeStatus, fetchStats, fetchWatch } from "../api";
import { h } from "../components/dom";
import {
  PRESENT_ROUTES,
  PRESENT_STEPS,
  watchAlertRoute,
  type PresentCaption,
  type PresentStep,
  type PresentToken,
} from "./steps";

const HORMUZ_UNSEEN_URL = "/api/hormuz/unseen";
/**
 * The Hormuz trend vs the Gulf of Oman control. A 404 (or an API without the
 * route) means "not proved", and the trend beat is skipped.
 */
const HORMUZ_TREND_URL = "/api/hormuz/trend";
const HORMUZ_VERDICT = "moves with event";
/** Matches the CSS breakpoint: the tour is desktop-only. */
const PHONE_QUERY = "(max-width: 1000px)";

const TOKEN = /\{([a-z_]+)\}/g;
const INTEGER = new Intl.NumberFormat("en-US");

/**
 * Live values for the caption tokens. A missing key means "no number from the
 * API", and the step falls back to its number-free sentence. Null means the
 * fetch has not answered yet.
 */
type TokenValues = Partial<Record<PresentToken, string>>;

interface TourValues {
  tokens: TokenValues;
  /** True only when the trend endpoint's regime verdict is "moves with event". */
  trend: boolean;
}

let tokenValues: TokenValues | null = null;
let trendValidated = false;

/** Route values (today: the spoof alert id); missing means the beat's fallback route. */
const routeValues: Record<string, string> = {};

/** The beats visible for this run: all of them, minus any unproved conditional beat. */
let steps: readonly PresentStep[] = visibleSteps(false);

let active = false;
let index = 0;
/** Id of the beat on screen, so a list change keeps the presenter on that beat. */
let currentId: string | null = null;
/** Bumped on start/exit so a fetch from an earlier run cannot repaint this one. */
let run = 0;
/** The hash that was on screen before presentation started; restored on exit. */
let startHash = "";

let root: HTMLElement | null = null;
let caption: HTMLElement | null = null;
let stepLabel: HTMLElement | null = null;
let headline: HTMLElement | null = null;
let line: HTMLElement | null = null;
let progress: HTMLElement | null = null;
let progressFill: HTMLElement | null = null;

/** The tour list after the run's one trend fetch answers. */
function visibleSteps(showTrend: boolean): readonly PresentStep[] {
  return PRESENT_STEPS.filter((beat) => beat.requires !== "hormuz_trend" || showTrend);
}

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
  const count = steps.length;
  if (progress !== null) {
    progress.setAttribute("aria-valuemax", String(count));
    progress.setAttribute("aria-valuenow", String(step + 1));
    progress.setAttribute("aria-valuetext", `Step ${step + 1} of ${count}`);
  }
  if (progressFill !== null) {
    progressFill.style.width = `${((step + 1) / count) * 100}%`;
  }
}

/** A finite number or null; one guard for every optional field on the wire. */
function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function integerToken(value: number | null): string | undefined {
  return value === null ? undefined : INTEGER.format(Math.round(value));
}

function ratioToken(value: number | null): string | undefined {
  return value === null ? undefined : value.toFixed(2);
}

/** "148 ms"; undefined leaves the beat's number-free fallback sentence. */
function recoveryToken(value: number | null): string | undefined {
  return value === null || value < 0 ? undefined : `${Math.round(value)} ms`;
}

/** The spoof event the "physics" beat is about: highest severity, as the API sorts. */
function topSpoof(alerts: readonly WatchAlert[]): WatchAlert | null {
  let best: WatchAlert | null = null;
  for (const alert of alerts) {
    if (alert.kind !== "spoof") continue;
    if (best === null || alert.severity > best.severity) best = alert;
  }
  return best;
}

interface SpoofFacts {
  ships: number | null;
  minKm: number | null;
  maxKm: number | null;
}

/**
 * The ghost point's own numbers. Current snapshots write `evidence.vessels[]`
 * with one `offset_km` each; older ones also carry a flat `offsets_km`. Both
 * are read without trusting unknown JSON.
 */
function spoofFacts(alert: WatchAlert): SpoofFacts {
  const evidence = alert.evidence ?? {};
  const rows = Array.isArray(evidence["vessels"]) ? evidence["vessels"] : [];
  const offsets: number[] = [];
  let rowCount = 0;

  for (const row of rows) {
    if (!isRecord(row)) continue;
    rowCount += 1;
    const km = finite(row["offset_km"]);
    if (km !== null && km > 0) offsets.push(km);
  }

  if (offsets.length === 0) {
    const flat = evidence["offsets_km"];
    if (Array.isArray(flat)) {
      for (const value of flat) {
        const km = finite(value);
        if (km !== null && km > 0) offsets.push(km);
      }
    }
  }

  const declared = finite(evidence["n_vessels"]);
  const ships =
    declared ?? (rowCount > 0 ? rowCount : offsets.length > 0 ? offsets.length : null);

  if (offsets.length === 0) return { ships, minKm: null, maxKm: null };
  return { ships, minKm: Math.min(...offsets), maxKm: Math.max(...offsets) };
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
  return (await response.json()) as T;
}

/** Hormuz's snapshot is local to its block; presentation needs only these fields. */
interface HormuzSummaryLite {
  dark_share_mean?: number | null;
  headline_share?: number | null;
}

interface HormuzUnseenLite {
  summary?: HormuzSummaryLite;
}

/** The trend's regime block: the only fields the tour prints or gates on. */
interface HormuzTrendLite {
  regime?: {
    treated?: { baseline?: number | null; current?: number | null };
    did?: number | null;
    verdict?: string | null;
  };
}

function normalVerdict(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

/** Fractions (0.316) become one-decimal percents for the caption ("31.6"). */
function percentToken(value: unknown): string | undefined {
  const fraction = finite(value);
  return fraction === null ? undefined : (fraction * 100).toFixed(1);
}

/**
 * The trend beat is shown only when the endpoint's own regime verdict says the
 * share moves with the event. Numbers come from the same response, never from
 * a constant here; a missing route (404) means no beat.
 */
async function loadHormuzTrend(): Promise<{ proved: boolean; tokens: TokenValues }> {
  try {
    const regime = (await getJson<HormuzTrendLite>(HORMUZ_TREND_URL)).regime;
    const proved = normalVerdict(regime?.verdict ?? "") === HORMUZ_VERDICT;
    const tokens: TokenValues = {};
    const before = percentToken(regime?.treated?.baseline);
    const after = percentToken(regime?.treated?.current);
    const did = percentToken(regime?.did);
    if (before !== undefined) tokens.hormuz_before = before;
    if (after !== undefined) tokens.hormuz_after = after;
    if (did !== undefined) tokens.did_pts = did;
    return { proved, tokens };
  } catch {
    // No route, or unreadable JSON: the trend beat is not proved.
    return { proved: false, tokens: {} };
  }
}

/**
 * Fetch every number the captions may print, once per tour run. The calls are
 * independent (`allSettled`) so one dead endpoint only removes its own numbers;
 * each beat then reads its fallback sentence. `trend` resolves to a verdict
 * rather than rejecting, so "missing" and "not proved" take the same path.
 */
async function loadTourValues(): Promise<TourValues> {
  const [watch, unseen, stats, edge, trend] = await Promise.allSettled([
    fetchWatch(),
    getJson<HormuzUnseenLite>(HORMUZ_UNSEEN_URL),
    fetchStats(),
    fetchEdgeStatus(),
    loadHormuzTrend(),
  ]);

  const values: TokenValues = {};

  if (watch.status === "fulfilled") {
    const data = watch.value;
    const counts = (data.counts ?? {}) as { ships?: unknown; alerts?: unknown };
    const ships = Array.isArray(data.ships) ? data.ships : [];
    const alerts = Array.isArray(data.alerts) ? data.alerts : [];
    const areas = Array.isArray(data.areas) ? data.areas : [];

    values.ships_now = integerToken(finite(counts.ships) ?? ships.length);
    values.alerts = integerToken(finite(counts.alerts) ?? alerts.length);

    // "across N areas" means the receivers we run, not every watched box.
    const listening = areas.filter((area) => area.kind === "listening").length;
    const areaCount = listening > 0 ? listening : areas.length;
    if (areaCount > 0) values.areas = integerToken(areaCount);

    const spoof = topSpoof(alerts);
    if (spoof !== null) {
      routeValues["spoof_id"] = spoof.id;
      const facts = spoofFacts(spoof);
      values.spoof_ships = integerToken(facts.ships);
      values.offset_min = integerToken(facts.minKm);
      values.offset_max = integerToken(facts.maxKm);
    }
  }

  if (unseen.status === "fulfilled") {
    const summary = unseen.value.summary;
    const share = finite(summary?.headline_share) ?? finite(summary?.dark_share_mean);
    if (share !== null) values.hormuz_share_pct = String(Math.round(share * 100));
  }

  if (stats.status === "fulfilled") {
    values.null_ratio = ratioToken(finite(stats.value.validation?.null_ratio));
  }

  if (edge.status === "fulfilled") {
    values.recovery = recoveryToken(finite(edge.value.last_recovery_ms));
  }

  if (trend.status === "fulfilled") Object.assign(values, trend.value.tokens);

  return {
    tokens: values,
    trend: trend.status === "fulfilled" && trend.value.proved,
  };
}

function ensureTourValues(myRun: number): void {
  void loadTourValues()
    .then((loaded) => {
      if (myRun !== run) return;
      tokenValues = loaded.tokens;
      trendValidated = loaded.trend;
      steps = visibleSteps(trendValidated);
      // The conditional beat may have just joined (or stayed absent): keep the
      // same beat on screen and only refresh its label, text and progress.
      repaint();
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
  };
}

/** The route for a beat, or the world view while the spoof id is unknown. */
function routeFor(beat: PresentStep): string {
  if (beat.alert === "spoof") {
    const id = routeValues["spoof_id"];
    return id === undefined || id === "" ? PRESENT_ROUTES.watch : watchAlertRoute(id);
  }
  return PRESENT_ROUTES[beat.route];
}

/** Swap the caption to one step and replay its 200 ms fade-in. */
function show(step: number): void {
  const beat = steps[step];
  if (beat === undefined || root === null) return;

  const text = captionFor(beat);
  index = step;
  currentId = beat.id;
  navigate(routeFor(beat));

  if (stepLabel !== null) stepLabel.textContent = `${step + 1} / ${steps.length}`;
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

/** Re-render the current beat after the step list or its values changed. */
function repaint(): void {
  if (!active) return;
  const at = currentId === null ? -1 : steps.findIndex((beat) => beat.id === currentId);
  show(at >= 0 ? at : 0);
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
      "aria-valuemax": steps.length,
      "aria-valuenow": 1,
      "aria-valuetext": `Step 1 of ${steps.length}`,
    },
    progressFill,
  );

  stepLabel = h("span", { class: "present-step" });
  headline = h("h1", { class: "present-headline" });
  line = h("p", { class: "present-line" });
  caption = h(
    "div",
    { class: "present-caption", role: "status", "aria-live": "polite" },
    h("div", { class: "present-meta" }, stepLabel),
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

function tourUnsupported(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(PHONE_QUERY).matches;
}

/** Start at step 1. Calling it while active restarts from the first beat. */
export function startPresent(): void {
  // The tour needs the width of a laptop: on a phone the overlay is not drawn.
  if (tourUnsupported()) return;

  if (!active) {
    startHash = location.hash;
    // Each run gets fresh numbers; they are cached until the run ends.
    run += 1;
    tokenValues = null;
    trendValidated = false;
    steps = visibleSteps(false);
    for (const key of Object.keys(routeValues)) delete routeValues[key];
    currentId = null;
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
  document.body.classList.remove("is-presenting");
  root?.remove();
  root = null;
  caption = null;
  stepLabel = null;
  headline = null;
  line = null;
  progress = null;
  progressFill = null;
  currentId = null;
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
    show(Math.min(index + 1, steps.length - 1));
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
