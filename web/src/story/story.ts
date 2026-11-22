/**
 * The recorded story screen (#274): one frozen dark-fleet case told in six
 * beats over an offline MapLibre map.
 *
 * The shell owns everything stateful: the map, the camera, the data fetches,
 * the keyboard, autoplay and the single rAF loop a beat may use. Beats (beats.ts)
 * only write copy and map ink; storyMap.ts only does geometry and layers. That
 * split keeps the copy rule checkable in one file and the map maths testable in
 * another.
 *
 * Data is same-origin, relative and optional: every endpoint that fails leaves
 * a sentence on screen instead of a blank beat. The satcheck rule (real pass,
 * else the frozen scenario, else "no pass") is implemented in resolveSatcheck.
 */

import "./story.css";

import * as maplibregl from "maplibre-gl";

import { offlineStyle } from "../basemap";
import { h } from "../components/dom";
import { fmtUtc } from "../format";
import { BEATS, BEAT_COUNT, type BeatContext, type BeatSpec, type BeatUi } from "./beats";
import {
  circlePolygon,
  easeInOut,
  fitStoryBounds,
  installStoryLayers,
  pointsBounds,
  trailCoords,
  type LngLat,
  type StoryMapLayers,
} from "./storyMap";
import type {
  EdgeView,
  SatcheckPayload,
  SatcheckView,
  SatCounts,
  SatDetection,
  SatScene,
  StoryCase,
  TrendView,
} from "./types";

const CASE_URL = "/api/story";
const SCENARIO_URL = "/api/story/satcheck";
const SATCHECK_PREFIX = "/api/satcheck/";
const EDGE_URL = "/api/edge/status";
const TREND_URL = "/api/hormuz/trend";

const AUTOPLAY_MS = 10_000;
/** The map idles in well under this offline; the cap only prevents a stuck beat. */
const IDLE_FALLBACK_MS = 1500;

/* ---- small guards --------------------------------------------------------- */

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseIso(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/** Verbatim text, or null for empty; the caller decides the line. */
function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function tagText(frozenAt: string): string {
  const ms = Date.parse(frozenAt);
  return Number.isFinite(ms) ? `Recorded case \u00b7 ${fmtUtc(frozenAt)} UTC` : "Recorded case";
}

/* ---- satcheck: the data half of the rule ---------------------------------- */

function withinDarkWindow(sceneTime: unknown, windowValue: unknown, nowMs: number): boolean {
  const time = parseIso(sceneTime);
  if (time === null) return false;
  let start: number | null = null;
  let end: number | null = null;
  if (Array.isArray(windowValue)) {
    start = parseIso(windowValue[0]);
    end = parseIso(windowValue[1]);
  } else if (windowValue !== null && typeof windowValue === "object") {
    const window = windowValue as { start?: unknown; end?: unknown };
    start = parseIso(window.start);
    end = parseIso(window.end);
  }
  if (start !== null && time < start) return false;
  return time <= (end ?? nowMs);
}

function detectionsFrom(raw: unknown): SatDetection[] {
  if (!Array.isArray(raw)) return [];
  const detections: SatDetection[] = [];
  for (const entry of raw) {
    if (entry === null || typeof entry !== "object") continue;
    const record = entry as { lat?: unknown; lon?: unknown; matched_mmsi?: unknown };
    if (!isFiniteNumber(record.lat) || !isFiniteNumber(record.lon)) continue;
    detections.push({
      lat: record.lat,
      lon: record.lon,
      matched_mmsi: isFiniteNumber(record.matched_mmsi) ? record.matched_mmsi : null,
    });
  }
  return detections;
}

function countsFrom(raw: unknown, detections: SatDetection[]): SatCounts {
  const partial = raw !== null && typeof raw === "object" ? (raw as Partial<SatCounts>) : {};
  const detected = isFiniteNumber(partial.detected)
    ? Math.max(0, Math.round(partial.detected))
    : detections.length;
  const matched = isFiniteNumber(partial.matched)
    ? Math.max(0, Math.round(partial.matched))
    : detections.filter((detection) => typeof detection.matched_mmsi === "number").length;
  const notBroadcasting = isFiniteNumber(partial.not_broadcasting)
    ? Math.max(0, Math.round(partial.not_broadcasting))
    : detections.filter((detection) => detection.matched_mmsi === null).length;
  return { detected, matched, not_broadcasting: notBroadcasting };
}

function chipUrls(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const urls: string[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      if (entry !== "") urls.push(entry);
      continue;
    }
    if (entry === null || typeof entry !== "object") continue;
    const record = entry as { chip_url?: unknown; url?: unknown };
    const url =
      typeof record.chip_url === "string"
        ? record.chip_url
        : typeof record.url === "string"
          ? record.url
          : null;
    if (url !== null && url !== "") urls.push(url);
  }
  return urls;
}

/**
 * The chip images of a satcheck payload. The API puts each one on its detection
 * (`detections[].chip_url`); `chips` and `chip_urls` are accepted too, so a payload
 * that lists them at the top level still works.
 */
function chipList(payload: SatcheckPayload): string[] {
  const direct = chipUrls(payload.chips);
  if (direct.length > 0) return direct;
  const listed = chipUrls(payload.chip_urls);
  if (listed.length > 0) return listed;
  return chipUrls(payload.detections);
}

/** The live answer, accepted only when the scene is inside the dark window. */
function realSatcheck(raw: unknown, nowMs: number): SatcheckView | null {
  if (raw === null || typeof raw !== "object") return null;
  const payload = raw as SatcheckPayload;
  const scene = payload.scene;
  if (scene === null || scene === undefined || typeof scene !== "object") return null;
  const sceneTime = (scene as SatScene).time;
  const windowValue =
    payload.dark_window ?? (scene as { dark_window?: unknown }).dark_window;
  if (!withinDarkWindow(sceneTime, windowValue, nowMs)) return null;
  const detections = detectionsFrom(payload.detections);
  return {
    kind: "real",
    sceneTime: typeof sceneTime === "string" ? sceneTime : null,
    detections,
    counts: countsFrom(payload.counts, detections),
    source: textOrNull(payload.source),
    banner: null,
    sub: null,
    chips: chipList(payload),
  };
}

/** The frozen scenario: its own numbers only; no window test, it is a reconstruction. */
function scenarioSatcheck(raw: unknown): SatcheckView | null {
  if (raw === null || typeof raw !== "object") return null;
  const payload = raw as SatcheckPayload;
  const detections = detectionsFrom(payload.detections);
  const scene = payload.scene;
  const sceneTime =
    scene !== null && scene !== undefined && typeof scene === "object"
      ? (scene as SatScene).time
      : null;
  return {
    kind: "scenario",
    sceneTime: typeof sceneTime === "string" ? sceneTime : null,
    detections,
    counts: countsFrom(payload.counts, detections),
    source: textOrNull(payload.source),
    banner: textOrNull(payload.banner),
    sub: textOrNull(payload.sub),
    chips: chipList(payload),
  };
}

function noneSatcheck(): SatcheckView {
  return {
    kind: "none",
    sceneTime: null,
    detections: [],
    counts: { detected: 0, matched: 0, not_broadcasting: 0 },
    source: null,
    banner: null,
    sub: null,
    chips: [],
  };
}

/* ---- the two small readouts ----------------------------------------------- */

function edgeFrom(raw: unknown): EdgeView {
  if (raw === null || typeof raw !== "object") return "offline";
  const status = raw as {
    state?: unknown;
    alarm?: { blind?: unknown } | null;
    error?: unknown;
  };
  if (typeof status.error === "string" && status.error.trim() !== "") return "offline";
  if (status.alarm?.blind === true) return "blind";
  if (typeof status.state !== "string") return "offline";
  return status.state === "OK" ? "ok" : "blind";
}

interface TrendLite {
  regime?: {
    treated?: {
      baseline?: { dark_share?: unknown };
      current?: { dark_share?: unknown };
    };
  };
}

function trendFrom(raw: unknown): TrendView | null {
  if (raw === null || typeof raw !== "object") return null;
  const regime = (raw as TrendLite).regime;
  const baseline = regime?.treated?.baseline?.dark_share;
  const current = regime?.treated?.current?.dark_share;
  if (!isFiniteNumber(baseline) || !isFiniteNumber(current)) return null;
  return { baseline, current };
}

/* ---- mount ---------------------------------------------------------------- */

export function renderStory(app: HTMLElement): { destroy(): void } {
  const bodyHadPresenting = document.body.classList.contains("is-presenting");
  document.body.classList.add("is-presenting");

  const root = h("div", {
    class: "story-root",
    role: "region",
    "aria-label": "Recorded case story",
  });
  const mapEl = h("div", { class: "story-map", "aria-hidden": "true" });
  const labelLayer = h("div", { class: "story-map-labels", "aria-hidden": "true" });

  const tag = h("div", { class: "story-tag" });
  tag.hidden = true;
  const strip = h("div", { class: "story-strip" });
  strip.hidden = true;
  const headline = h("h1", { class: "story-headline" });
  const sub = h("p", { class: "story-sub" });
  const extra = h("div", { class: "story-extra" });
  const card = h("div", { class: "story-card" }, strip, headline, sub, extra);
  const left = h("div", { class: "story-left" }, tag, card);

  const progressDots = Array.from({ length: BEAT_COUNT }, () =>
    h("span", { class: "story-dot", "aria-hidden": "true" }),
  );
  const counter = h("span", { class: "story-count" });
  const hints = h(
    "div",
    { class: "story-hints" },
    h("span", { class: "story-hint" }, h("kbd", { class: "story-kbd" }, "\u2192"), " next"),
    " \u00b7 ",
    h("span", { class: "story-hint" }, h("kbd", { class: "story-kbd" }, "\u2190"), " back"),
    " \u00b7 ",
    h("span", { class: "story-hint" }, h("kbd", { class: "story-kbd" }, "A"), " autoplay"),
    " \u00b7 ",
    h("span", { class: "story-hint" }, h("kbd", { class: "story-kbd" }, "Esc"), " exit"),
  );
  const controls = h(
    "div",
    { class: "story-controls" },
    h("div", { class: "story-dots", "aria-hidden": "true" }, ...progressDots),
    counter,
    hints,
  );

  root.append(mapEl, labelLayer, left, controls);
  app.replaceChildren(root);

  headline.textContent = "Loading the recorded case\u2026";

  const map = new maplibregl.Map({
    container: mapEl,
    style: offlineStyle(),
    center: [0, 35],
    zoom: 1.6,
    attributionControl: false,
    interactive: false,
  });

  /* ---- state ------------------------------------------------------------- */

  const abort = new AbortController();
  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let destroyed = false;
  let ready = false;
  let started = false;
  let story: StoryCase | null = null;
  let layers: StoryMapLayers | null = null;
  let satcheck: SatcheckView | null = null;
  let edge: EdgeView | null = null;
  let trend: TrendView | null = null;
  let current = 0;
  let currentRaf: number | null = null;
  let animationToken = 0;
  let autoplay = false;
  let autoplayTimer: number | null = null;
  let readyTimer: number | null = null;
  let labelEl: HTMLElement | null = null;
  let labelAt: LngLat | null = null;
  let lastFitKey: string | null = null;

  /* ---- fetches ----------------------------------------------------------- */

  async function fetchJson(path: string): Promise<unknown> {
    const response = await fetch(path, {
      headers: { Accept: "application/json" },
      signal: abort.signal,
    });
    if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
    return (await response.json()) as unknown;
  }

  /** 404, error, malformed: all mean "this endpoint has nothing for the beat". */
  async function fetchOptional(path: string): Promise<unknown | null> {
    try {
      return await fetchJson(path);
    } catch {
      return null;
    }
  }

  function normaliseCase(raw: StoryCase): StoryCase {
    return {
      ...raw,
      alert: {
        ...raw.alert,
        trail: Array.isArray(raw.alert.trail) ? raw.alert.trail : [],
      },
      contacts: Array.isArray(raw.contacts) ? raw.contacts : [],
      background: Array.isArray(raw.background) ? raw.background : [],
    };
  }

  async function loadCase(): Promise<void> {
    try {
      const raw = await fetchJson(CASE_URL);
      if (destroyed) return;
      const candidate = raw as StoryCase;
      if (
        candidate === null ||
        typeof candidate !== "object" ||
        candidate.alert === null ||
        candidate.alert === undefined ||
        candidate.alert.last === null ||
        candidate.alert.last === undefined ||
        !isFiniteNumber(candidate.alert.last.lat) ||
        !isFiniteNumber(candidate.alert.last.lon)
      ) {
        throw new Error("the story case has no alert fix");
      }
      story = normaliseCase(candidate);
      tag.textContent = tagText(story.frozen_at);
      tag.hidden = false;
      maybeStart();
      void resolveSatcheck(story);
    } catch {
      if (destroyed) return;
      tag.hidden = true;
      headline.textContent = "The recorded case could not be loaded";
      sub.textContent = "The story needs the frozen case from /api/story.";
    }
  }

  /**
   * Rule a: the live pass, only inside the dark window. Rule b: the frozen
   * scenario when the case says scenario. Rule c: resolved "none" (beat 4's
   * own fallback sentence). A failed real fetch is the same as a 404.
   */
  async function resolveSatcheck(candidate: StoryCase): Promise<void> {
    const real = await fetchOptional(
      `${SATCHECK_PREFIX}${encodeURIComponent(candidate.alert.id)}`,
    );
    if (destroyed) return;
    const realView = real === null ? null : realSatcheck(real, Date.now());
    if (realView !== null) {
      satcheck = realView;
      satcheckResolved();
      return;
    }

    if (candidate.satcheck?.mode === "scenario") {
      const scenario = await fetchOptional(SCENARIO_URL);
      if (destroyed) return;
      satcheck = scenario === null ? noneSatcheck() : scenarioSatcheck(scenario) ?? noneSatcheck();
      satcheckResolved();
      return;
    }

    satcheck = noneSatcheck();
    satcheckResolved();
  }

  function satcheckResolved(): void {
    if (!destroyed && started && current === 3) show(current, { rearm: false });
  }

  async function loadEdge(): Promise<void> {
    const raw = await fetchOptional(EDGE_URL);
    if (destroyed) return;
    edge = edgeFrom(raw);
    if (started && current === 4) show(current, { rearm: false });
  }

  async function loadTrend(): Promise<void> {
    const raw = await fetchOptional(TREND_URL);
    if (destroyed) return;
    trend = trendFrom(raw);
    if (started && current === 5) show(current, { rearm: false });
  }

  /* ---- labels ------------------------------------------------------------ */

  function positionLabel(): void {
    if (labelEl === null || labelAt === null) return;
    const point = map.project(labelAt);
    labelEl.style.transform = `translate(${point.x}px, ${point.y}px) translate(12px, -50%)`;
  }

  function clearLabel(): void {
    labelEl?.remove();
    labelEl = null;
    labelAt = null;
  }

  function setLabel(text: string, at: LngLat | null): void {
    clearLabel();
    if (at === null) return;
    const el = h("div", { class: "story-map-label" });
    el.textContent = text;
    labelLayer.append(el);
    labelEl = el;
    labelAt = at;
    positionLabel();
  }

  /* ---- the card ---------------------------------------------------------- */

  const ui: BeatUi = {
    setHeadline(text: string): void {
      headline.textContent = text;
    },
    setSub(text: string): void {
      sub.textContent = text;
    },
    setStrip(text: string | null): void {
      if (text === null) {
        strip.textContent = "";
        strip.hidden = true;
        return;
      }
      strip.textContent = text;
      strip.hidden = false;
    },
    extra(): HTMLElement {
      return extra;
    },
    setLabel,
  };

  /* ---- animation --------------------------------------------------------- */

  function cancelAnimation(): void {
    animationToken += 1;
    if (currentRaf !== null) {
      window.cancelAnimationFrame(currentRaf);
      currentRaf = null;
    }
  }

  function animate(
    durationMs: number,
    onTick: (eased: number) => void,
    onDone?: () => void,
  ): void {
    cancelAnimation();
    const token = animationToken;
    if (reducedMotion) {
      onTick(1);
      onDone?.();
      return;
    }
    const startedAt = performance.now();
    const step = (now: number): void => {
      if (destroyed || token !== animationToken) return;
      const progress = durationMs <= 0 ? 1 : Math.min(1, (now - startedAt) / durationMs);
      onTick(easeInOut(progress));
      if (progress < 1) {
        currentRaf = window.requestAnimationFrame(step);
      } else {
        currentRaf = null;
        onDone?.();
      }
    };
    currentRaf = window.requestAnimationFrame(step);
  }

  /* ---- autoplay ---------------------------------------------------------- */

  function clearAutoplayTimer(): void {
    if (autoplayTimer === null) return;
    window.clearTimeout(autoplayTimer);
    autoplayTimer = null;
  }

  function armAutoplay(): void {
    clearAutoplayTimer();
    if (!autoplay || destroyed || current >= BEAT_COUNT - 1) return;
    autoplayTimer = window.setTimeout(() => {
      autoplayTimer = null;
      if (destroyed || !autoplay) return;
      if (current < BEAT_COUNT - 1) show(current + 1);
    }, AUTOPLAY_MS);
  }

  function toggleAutoplay(): void {
    autoplay = !autoplay;
    if (autoplay) armAutoplay();
    else clearAutoplayTimer();
  }

  /* ---- camera ------------------------------------------------------------ */

  function caseCoords(): LngLat[] {
    if (story === null) return [];
    const alert = story.alert;
    const coords = trailCoords(alert.trail, alert.last);
    if (Number.isFinite(story.reach_km) && story.reach_km > 0.05) {
      coords.push(...circlePolygon([alert.last.lon, alert.last.lat], story.reach_km));
    }
    for (const contact of story.contacts) {
      if (
        contact.could_meet === true &&
        Number.isFinite(contact.lat) &&
        Number.isFinite(contact.lon)
      ) {
        coords.push([contact.lon, contact.lat]);
      }
    }
    return coords;
  }

  function fitBeat(beat: BeatSpec, immediate: boolean): void {
    let coords: LngLat[] | null = null;
    if (beat.camera === "case") {
      coords = caseCoords();
    } else if (beat.camera === "satcheck") {
      const detections = satcheck?.detections ?? [];
      coords =
        detections.length > 0
          ? detections.map((detection): LngLat => [detection.lon, detection.lat])
          : caseCoords();
    }
    if (coords === null || coords.length === 0) return;

    const bounds = pointsBounds(coords);
    if (bounds === null) return;
    const key = `${bounds[0][0].toFixed(4)},${bounds[0][1].toFixed(4)},${bounds[1][0].toFixed(4)},${bounds[1][1].toFixed(4)}`;
    if (!immediate && key === lastFitKey) return;
    lastFitKey = key;
    fitStoryBounds(map, coords, { animate: !immediate && !reducedMotion });
  }

  /* ---- beats ------------------------------------------------------------- */

  function clearBeat(): void {
    strip.textContent = "";
    strip.hidden = true;
    headline.textContent = "";
    sub.textContent = "";
    extra.replaceChildren();
    clearLabel();
    layers?.clear();
  }

  function updateControls(): void {
    progressDots.forEach((dot, index) => {
      dot.classList.toggle("is-current", index === current);
    });
    counter.textContent = `${current + 1} / ${BEAT_COUNT}`;
  }

  function show(index: number, opts: { immediate?: boolean; rearm?: boolean } = {}): void {
    if (destroyed || !started || story === null || layers === null) return;
    current = Math.max(0, Math.min(BEAT_COUNT - 1, index));
    cancelAnimation();
    clearBeat();
    updateControls();

    const beat = BEATS[current];
    fitBeat(beat, opts.immediate === true);

    const ctx: BeatContext = {
      story,
      layers,
      ui,
      satcheck,
      edge,
      trend,
      animate,
    };
    beat.draw(ctx);

    if (opts.rearm !== false) armAutoplay();
  }

  function maybeStart(): void {
    if (started || destroyed || !ready || story === null || layers === null) return;
    started = true;
    show(0, { immediate: true, rearm: false });
  }

  /* ---- keyboard ---------------------------------------------------------- */

  function onKeyDown(event: KeyboardEvent): void {
    if (destroyed) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isTyping(event.target)) return;

    if (event.key === "Escape") {
      event.preventDefault();
      location.hash = "#/watch";
      return;
    }
    if (!started) return;

    if (event.key === "ArrowRight" || event.key === " " || event.code === "Space") {
      event.preventDefault();
      if (event.repeat) return;
      if (current < BEAT_COUNT - 1) show(current + 1);
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (event.repeat) return;
      show(Math.max(0, current - 1));
      return;
    }

    if (event.key === "a" || event.key === "A") {
      event.preventDefault();
      if (event.repeat) return;
      toggleAutoplay();
    }
  }

  /* ---- map lifecycle ----------------------------------------------------- */

  const onMapMove = (): void => {
    positionLabel();
  };

  function onResize(): void {
    if (destroyed) return;
    map.resize();
    positionLabel();
  }

  function whenMapReady(onReady: () => void): void {
    let loaded = false;
    let idle = false;
    let finished = false;

    const cleanup = (): void => {
      map.off("load", onLoad);
      map.off("idle", onIdle);
      if (readyTimer !== null) {
        window.clearTimeout(readyTimer);
        readyTimer = null;
      }
    };

    const finish = (): void => {
      if (finished) return;
      finished = true;
      cleanup();
      if (!destroyed) onReady();
    };

    const check = (): void => {
      if (loaded && idle) finish();
    };

    const onLoad = (): void => {
      loaded = true;
      // The offline style has no tiles to chase; still, an idle that never comes
      // must not hold the story on its loading line.
      if (readyTimer === null) {
        readyTimer = window.setTimeout(() => {
          readyTimer = null;
          idle = true;
          check();
        }, IDLE_FALLBACK_MS);
      }
      check();
    };

    const onIdle = (): void => {
      idle = true;
      check();
    };

    map.on("load", onLoad);
    map.on("idle", onIdle);
    if (map.loaded()) {
      // Already loaded before this screen attached: there will be no new idle.
      loaded = true;
      idle = true;
      finish();
    }
  }

  /* ---- boot -------------------------------------------------------------- */

  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("resize", onResize);
  map.on("move", onMapMove);
  updateControls();

  whenMapReady(() => {
    if (destroyed) return;
    ready = true;
    layers = installStoryLayers(map);
    maybeStart();
  });

  void loadCase();
  void loadEdge();
  void loadTrend();

  /* ---- teardown ---------------------------------------------------------- */

  function destroy(): void {
    if (destroyed) return;
    destroyed = true;
    cancelAnimation();
    clearAutoplayTimer();
    if (readyTimer !== null) {
      window.clearTimeout(readyTimer);
      readyTimer = null;
    }
    abort.abort();
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("resize", onResize);
    map.off("move", onMapMove);
    clearLabel();
    try {
      map.remove();
    } catch {
      // Removing twice is harmless; the screen is going away either way.
    }
    root.remove();
    if (!bodyHadPresenting) document.body.classList.remove("is-presenting");
  }

  return { destroy };
}
