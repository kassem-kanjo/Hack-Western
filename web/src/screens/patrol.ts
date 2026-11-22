/**
 * Patrol unit tab (#36): what did the patrol unit just see, and what did it decide?
 *
 * Left: the latest camera frame, boxes already drawn by the Pi, refreshed only
 * when the decision names a new frame. Right: the three-step chain that led to
 * the verdict (camera -> untrusted AI -> safety core), the verdict itself, the
 * patrol position, and the reliability numbers that make the real-time claim
 * checkable. Below them the two demo actions.
 *
 * The decision is the source of truth and is polled once a second while the tab
 * is visible; the supervisor status supplies scheduling, loop lateness and the
 * controls. The frame is double-buffered, so a 1 fps swap never flashes.
 *
 * The screen owns its timer because the router does not hold a handle to it.
 * Every tick checks `main.isConnected`; the moment another tab replaces this
 * screen the interval stops, and nothing is fetched in a hidden tab.
 */

import "../styles/patrol.css";

import type {
  EdgeAb,
  EdgeAbArm,
  EdgeAi,
  EdgeDecision,
  EdgeEvent,
  EdgeSchedule,
  EdgeStatus,
  EdgeVerdictLevel,
} from "../api";
import {
  edgeFrameUrl,
  fetchEdgeAb,
  fetchEdgeDecision,
  fetchEdgeStatus,
  killEdgeDetector,
  stressEdgeDetector,
} from "../api";
import { h } from "../components/dom";
import { guideLine } from "../components/GuideLine";
import { fetchSightings, type SightingsPayload } from "../components/patrolSightings";
import { topBar } from "../components/TopBar";
import { fmtInt, fmtUtc } from "../format";

/** Poll rate: the decision is rewritten per frame (3-5 per second on the Pi). */
const POLL_MS = 400;
/** How long the measured recovery number stays on the button after recovery. */
const RECOVERY_HOLD_MS = 5000;
/** The kill state stays on screen at least this long, even if recovery is faster. */
const KILL_MIN_SHOW_MS = 1200;
/** A decision older than this (unit clock) means no frames are arriving. */
const STALE_FRAME_MS = 3000;
/** The evidence log is re-hashed on the Pi per read: fetch it every 10 s, not per frame. */
const SIGHTINGS_MS = 10000;
/** A detector event younger than this means the supervisor is mid-restart. */
const RESTART_WINDOW_MS = 5000;

/**
 * One recorded sighting for a visitor with no ship photo. Every value is the Pi's own output for
 * frame 314 of a real run (21 Nov, 21:26:37Z). The photo in that frame is a third-party picture,
 * so it is not shown: the camera area is an example panel that carries the same label, score and
 * alert text. It is never presented as live.
 */
const RECORDED_SIGHTING = {
  frame: 314,
  when: "21 Nov 21:26:37Z",
  label: "boat",
  scorePct: 80,
  inferenceMs: 108,
  alert: "ALERT · Vessel seen, not broadcasting",
  position: "Kiel Bight (54.45, 10.25)",
} as const;

/** The last evidence-log payload, so the recorded toggle can repaint the evidence line at once. */
let lastSightings: SightingsPayload | null = null;

/** The A/B card's rows, in the order armCells() fills them. Plain label; the detail is the tooltip. */
const AB_ROWS: readonly { label: string; title: string }[] = [
  { label: "Alarms on time", title: "Alarms raised before the deadline, out of the alarm frames the camera sent." },
  { label: "Slowest alert", title: "Worst alarm latency in the test, in ms. Each alarm is judged against the deadline." },
  { label: "Safety core's worst delay", title: "Worst lateness of the safety core's 20 Hz loop in the test, in ms." },
];

/** A chain square's meaning: running, coming back, stopped, or nothing yet. */
type Tone = "ok" | "warn" | "bad" | "idle";

/** `t_wall` is Unix seconds; `t` (if present) is ISO. Either may be missing. */
function eventTimeMs(event: EdgeEvent): number | null {
  if (typeof event.t_wall === "number" && Number.isFinite(event.t_wall)) {
    return event.t_wall * 1000;
  }
  if (typeof event.t === "string") {
    const at = Date.parse(event.t);
    if (Number.isFinite(at)) return at;
  }
  return null;
}

/** The decision's wall clock: ISO, Unix seconds, or (defensively) milliseconds. */
function wallMs(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e12 ? value : value * 1000;
  }
  if (typeof value === "string") {
    const at = Date.parse(value);
    return Number.isFinite(at) ? at : null;
  }
  return null;
}

/** "21 Nov 20:41:07Z": seconds included, because a decision is a moment. */
function fmtWall(ms: number): string {
  const iso = new Date(ms).toISOString();
  return `${fmtUtc(iso)}:${iso.slice(17, 19)}Z`;
}

/** 20 -> "20", 12.5 -> "12.5": camera and loop rates read without noise. */
function fmtRate(value: number): string {
  return value >= 10 ? String(Math.round(value)) : String(Math.round(value * 10) / 10);
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Recovery is quoted in seconds once it is over a second ("2.8 s"). */
function fmtRecovery(value: number | null | undefined): string {
  const ms = finiteOrNull(value);
  if (ms === null) return "none yet";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/** A radius keeps its integer form ("2 km"), a distance keeps one decimal ("1.2 km"). */
function fmtRadius(km: number): string {
  return Number.isInteger(km) ? String(km) : km.toFixed(1);
}

/** "SCHED_FIFO 30" on QNX, "SCHED_OTHER 8" (or its nice value) elsewhere. */
function schedValue(sched: EdgeSchedule | null | undefined): string {
  if (!sched) return "not reported";
  const policy = (sched.policy ?? "").trim().toUpperCase();
  const priority = finiteOrNull(sched.priority);
  const nice = finiteOrNull(sched.nice);
  if (policy !== "") {
    if (priority !== null) return `${policy} ${Math.round(priority)}`;
    if (nice !== null) return `${policy} nice ${nice >= 0 ? "+" : ""}${Math.round(nice)}`;
    return policy;
  }
  return priority !== null ? String(Math.round(priority)) : "not reported";
}

/** Sub-millisecond lateness is the point: 0.3 ms must not round to "0 ms". */
function fmtMs(value: number): string {
  return value < 10 ? value.toFixed(1) : String(Math.round(value));
}

/**
 * Reliability line 1 in plain words: the safety core's priority, and its worst
 * lateness in the last 60 s. Missing values name what is missing.
 */
function safetyLine(s: EdgeStatus): string {
  const priority = schedValue(s.priorities?.safety_core) === "not reported" ? "priority not reported" : "top priority";
  const worst = finiteOrNull(s.safety_tick?.max_late_ms);
  const late = worst === null ? "worst lateness not reported" : `worst ${fmtMs(worst)} ms late in the last 60 s`;
  // The rail shows the idle loop unless a stress run is on; say which one this is.
  const load = s.stress?.active === true ? " (during overload)" : " (no overload)";
  return `Safety core: ${priority} · ${late}${load}`;
}

/** The safety core's own loop: 20 Hz, its lateness, and misses in the last minute (tooltip only). */
function loopDetail(tick: EdgeStatus["safety_tick"]): string {
  if (!tick) return "not reported";
  const parts: string[] = [];
  const hz = finiteOrNull(tick.hz);
  if (hz === null) parts.push("not reported");
  else parts.push(`${fmtRate(hz)} Hz`);
  const p50 = finiteOrNull(tick.p50_late_ms);
  if (p50 !== null) parts.push(`median late ${fmtMs(p50)} ms`);
  const p99 = finiteOrNull(tick.p99_late_ms);
  if (p99 !== null) parts.push(`p99 ${fmtMs(p99)} ms`);
  const max = finiteOrNull(tick.max_late_ms);
  if (max !== null) parts.push(`max ${fmtMs(max)} ms`);
  const late = finiteOrNull(tick.late_count_60s);
  const ticks = finiteOrNull(tick.ticks_60s);
  const of = ticks === null ? "" : ` of ${fmtInt(ticks)}`;
  parts.push(`${late === null ? "not reported" : fmtInt(late)}${of} ticks >5 ms late (60 s)`);
  return parts.join(" \u00b7 ");
}

/** The highest-confidence box the detector reported, or null for none. */
function topBox(ai: EdgeAi | null | undefined): { label: string; score: number } | null {
  const boxes = ai?.boxes;
  if (!Array.isArray(boxes)) return null;
  let best: { label: string; score: number } | null = null;
  for (const box of boxes) {
    const score = finiteOrNull(box?.score);
    if (score === null) continue;
    if (best === null || score > best.score) {
      const label = typeof box.label === "string" && box.label.trim() !== "" ? box.label.trim() : "object";
      best = { label, score };
    }
  }
  return best;
}

/** "boat 82%": the one detection line a judge can read at 1 m. */
function aiText(ai: EdgeAi | null | undefined): string {
  const box = topBox(ai);
  if (box === null) return "No vessel in view";
  const pct = box.score <= 1 ? Math.round(box.score * 100) : Math.round(box.score);
  return `${box.label} ${pct}%`;
}

type BannerLevel = EdgeVerdictLevel | "blind";

/**
 * The unit's own wording, translated to the screen's copy rules. AIS is an
 * acronym for small print; the headline says what the ship is doing instead.
 */
function plainText(raw: string): string {
  return raw
    .replace(/VESSEL NOT ON AIS/gi, "Vessel not broadcasting its position")
    .replace(/Matches AIS/gi, "Matches a ship broadcasting");
}

function verdictLevel(value: unknown): EdgeVerdictLevel {
  return value === "alert" || value === "ok" ? value : "idle";
}

/**
 * What the banner says. The safety core's own stats win for BLIND (it knows first); otherwise the
 * decision's alarm drives it. Range and time-to-collision are estimates and are labelled so.
 */
function banner(
  status: EdgeStatus | null,
  decision: EdgeDecision | null,
): { level: BannerLevel; text: string; sub: string } {
  if (status?.alarm?.blind === true || decision?.alarm?.level === "blind") {
    return { level: "blind", text: "Lookout blind", sub: "no frames: the lookout cannot see" };
  }
  if (decision === null) return { level: "idle", text: "Waiting for a decision", sub: "" };
  const alarm = decision.alarm;
  if (alarm && (alarm.level === "warning" || alarm.level === "critical")) {
    const parts: string[] = [];
    if (alarm.trend === "closing") parts.push("closing");
    else if (alarm.trend === "opening") parts.push("opening");
    else if (alarm.trend === "steady") parts.push("steady");
    if (typeof alarm.ttc_s === "number") parts.push(`~${Math.round(alarm.ttc_s)} s at this rate`);
    if (typeof alarm.range_m === "number") parts.push(`range ~${fmtInt(alarm.range_m)} m (estimate)`);
    return { level: "alert", text: "Vessel not broadcasting its position", sub: parts.join(" \u00b7 ") };
  }
  const matched = typeof decision.verdict?.text === "string" && decision.verdict.text.startsWith("Matches AIS");
  return {
    level: matched ? "ok" : "idle",
    text: matched ? "Watching \u00b7 vessel broadcasting its position" : "Watching \u00b7 no vessel in view",
    sub: matched ? plainText(decision.verdict.text) : "",
  };
}

/** The safety core step: what the transponders said when the verdict was made. */
function aisLine(decision: EdgeDecision | null): string {
  if (decision === null) return "waiting for a decision";
  const text = typeof decision.verdict?.text === "string" ? decision.verdict.text.trim() : "";
  if (text.startsWith("Matches AIS")) return plainText(text);

  const nearby = Array.isArray(decision.ais?.nearby) ? decision.ais.nearby : [];
  const noun = nearby.length === 1 ? "ship" : "ships";
  const radius = finiteOrNull(decision.ais?.radius_km);
  const tail = decision.ai?.down === true ? " \u00b7 still checking" : "";
  if (radius === null) return `${fmtInt(nearby.length)} ${noun} broadcasting nearby${tail}`;
  return `${fmtInt(nearby.length)} ${noun} broadcasting within ${fmtRadius(radius)} km${tail}`;
}

/** The why-line under the verdict: one plain sentence the judge can read at 2 m. */
function whyLine(level: BannerLevel, d: EdgeDecision | null, recorded: boolean): string {
  if (recorded) {
    return `The camera sees a ${RECORDED_SIGHTING.label}, and no ship is broadcasting within 2 km.`;
  }
  if (level === "blind") return "The camera is sending no frames, so the lookout cannot see the water.";
  if (level === "alert") {
    const radius = finiteOrNull(d?.ais?.radius_km) ?? 2;
    return `The camera sees a vessel and no ship is broadcasting within ${fmtRadius(radius)} km.`;
  }
  if (level === "ok") return "The camera sees a vessel, and a ship is broadcasting its position.";
  if (d === null) return "Waiting for the first decision from the camera.";
  return "The camera sees no vessel, so there is no ship to check against.";
}

function verdictText(decision: EdgeDecision | null): string {
  if (decision === null) return "Waiting for a decision";
  const text = typeof decision.verdict?.text === "string" ? decision.verdict.text.trim() : "";
  return text !== "" ? text : "No decision";
}

interface Step {
  row: HTMLElement;
  value: HTMLElement;
}

/**
 * Paint the Patrol screen into `app`.
 *
 * Returns synchronously. Status and decision fetches continue in the background;
 * the interval stops itself when the screen is replaced or the tab is hidden.
 */
export function renderPatrol(app: HTMLElement): void {
  let disposed = false;
  /** True from a successful status fetch until the next failure. */
  let online = false;
  /** One poll at a time: a slow API must not queue a second one. */
  let pollInFlight = false;
  let pollTimer: number | null = null;
  /** The evidence-log timer (SIGHTINGS_MS) and its one-request-at-a-time guard. */
  let sightingsTimer: number | null = null;
  let sightingsInFlight = false;
  let lastOkMs: number | null = null;

  /** Latest good payloads; the screen keeps the last decision while a fetch fails. */
  let status: EdgeStatus | null = null;
  let ab: EdgeAb | null = null;
  let abPolledAt = 0;
  let decision: EdgeDecision | null = null;
  /** The last frame id we asked the camera for, so a still unit costs one request. */
  let lastFrameId: number | null = null;

  // Frame state. The visible <img> is only swapped once the next frame has
  // loaded, so a new frame never flashes the black stage.
  let frameLoaded = false;

  // Kill/recovery state.
  let controls = false;
  let killing = false;
  let stressing = false;
  let stressActive = false;
  let killedAt: number | null = null;
  let recoveredMs: number | null = null;
  // Restart count when the kill was requested: the supervisor's own recovery_ms is
  // only final once a restart past this count has delivered its first frame.
  let restartsAtKill: number | null = null;
  let lastRestarts = 0;
  // The previous measured recovery, so an old number is never shown for a new kill.
  let recoveryAtKill: number | null = null;
  let recoveredUntil = 0;

  /** The verdict's key, so the row animates on change and never on a repeat paint. */
  let lastVerdictKey: string | null = null;
  const noMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const bar = topBar({
    active: "patrol",
    mode: "LIVE",
    generatedAt: null,
    caseHref: null,
    demoHref: null,
  });
  const badge = bar.querySelector<HTMLElement>(".mode-badge");

  // ---- stage: the frame and its one caption row ----

  const frameImg = h("img", {
    class: "patrol-frame-img",
    alt: "Latest camera frame from the patrol unit, with the AI detector's boxes drawn on it",
    hidden: true,
  });
  const framePlaceholder = h(
    "div",
    { class: "patrol-frame-empty" },
    "Waiting for the camera\u2026",
  );

  /** The photo instruction over the frame; hidden once a vessel is detected or the camera is offline. */
  const frameHint = h(
    "div",
    { class: "patrol-frame-hint is-hidden" },
    "Hold a ship photo 30–60 cm in front of the camera",
  );

  /** The offscreen loader; a decoded frame replaces the visible one atomically. */
  const frameBuffer = new Image();

  /** The camera is judged by the unit itself: helper down, source starved, or no new decision. */
  let cameraOffline = false;

  function syncFrame(): void {
    const show = online && frameLoaded && !cameraOffline;
    frameImg.hidden = !show;
    framePlaceholder.hidden = show;
    framePlaceholder.textContent = cameraOffline ? "Camera offline" : "Waiting for the camera\u2026";
    // The hint covers the frame only while the AI sees no vessel and the camera is live.
    const detected = decision !== null && (decision.ai?.boxes?.length ?? 0) > 0;
    frameHint.classList.toggle("is-hidden", !show || detected);
  }

  /** Same clock on both sides (the Pi's), so laptop/Pi clock skew cannot fake an outage. */
  function isCameraOffline(s: EdgeStatus, d: EdgeDecision | null): boolean {
    // Only a unit with a camera helper (source "<unit>:<path>") can report the helper dead;
    // a net or directory source legitimately has none.
    if (s.camera?.alive === false && /^\d+:/.test(s.camera.source ?? "")) return true;
    if (s.detector_alive && s.detector_waiting) return true;
    const now = finiteOrNull(s.t_wall);
    const at = d === null ? null : wallMs(d.t);
    if (now !== null && at !== null && d?.ai?.down !== true && now * 1000 - at > STALE_FRAME_MS) return true;
    return false;
  }

  /** One image request at a time: a new src would abort the one still in flight. */
  let frameLoading = false;

  frameBuffer.onload = (): void => {
    frameLoading = false;
    if (disposed) return;
    frameLoaded = true;
    frameImg.src = frameBuffer.src;
    syncFrame();
  };
  frameBuffer.onerror = (): void => {
    // One dropped JPEG is not a reason to blank the last good frame.
    frameLoading = false;
    // Ask again on the next decision, even if its frame id has not moved.
    lastFrameId = null;
    if (disposed) return;
    syncFrame();
  };

  function loadFrame(): void {
    if (disposed || frameLoading) return;
    frameLoading = true;
    frameBuffer.src = edgeFrameUrl(Date.now());
  }

  const caption = h("div", { class: "patrol-caption" }, "Waiting for the first decision");

  /**
   * The recorded example, laid over the frame. It says it is recorded and not live, and it covers
   * the live image while open so the two can never be read as one.
   */
  const recordedPanel = h(
    "div",
    { class: "patrol-recorded", hidden: true, role: "region", "aria-label": "Recorded sighting, not live" },
    h("p", { class: "patrol-recorded-tag" }, "Recorded on this Pi, 21 Nov · not live"),
    h(
      "div",
      { class: "patrol-recorded-art", "aria-hidden": "true" },
      h("span", { class: "patrol-recorded-box" }, `${RECORDED_SIGHTING.label} ${RECORDED_SIGHTING.scorePct}%`),
      h("span", { class: "patrol-recorded-example" }, "example"),
    ),
    h("p", { class: "patrol-recorded-alert" }, RECORDED_SIGHTING.alert),
    h(
      "p",
      { class: "patrol-recorded-note" },
      `Frame ${RECORDED_SIGHTING.frame} at ${RECORDED_SIGHTING.when}, AI ${RECORDED_SIGHTING.inferenceMs} ms, ${RECORDED_SIGHTING.position}. The label, score and alert are the Pi's output for that frame; the photo is not shown.`,
    ),
  );

  /** True while the recorded example covers the frame: the chain and evidence show recorded values. */
  function recordedShown(): boolean {
    return !recordedPanel.hidden;
  }

  const stage = h(
    "section",
    { class: "stage patrol-stage" },
    h("div", { class: "patrol-frame" }, frameImg, framePlaceholder, frameHint, recordedPanel),
    caption,
  );

  // ---- right rail: the chain, the verdict, the numbers, the actions ----

  function step(name: string, note?: string): Step {
    const value = h("span", { class: "patrol-step-value" }, "None yet");
    const row = h(
      "li",
      { class: "patrol-step is-idle" },
      h("span", { class: "patrol-step-square", "aria-hidden": "true" }),
      h(
        "div",
        { class: "patrol-step-body" },
        h(
          "span",
          { class: "patrol-step-name" },
          name,
          note === undefined ? null : h("span", { class: "patrol-step-note" }, ` ${note}`),
        ),
        value,
      ),
    );
    return { row, value };
  }

  /** `small` is the technical detail, printed under the plain line. */
  function setStep(target: Step, tone: Tone, text: string, small?: string): void {
    target.row.className = `patrol-step is-${tone}`;
    const detail = small === undefined || small === "" ? null : h("span", { class: "patrol-small" }, small);
    target.value.replaceChildren(text, ...(detail === null ? [] : [detail]));
  }

  const cameraStep = step("Camera");
  const aiStep = step("AI detector", "(untrusted)");
  const safetyStep = step("Safety core");
  const chain = h(
    "ol",
    { class: "patrol-chain" },
    cameraStep.row,
    aiStep.row,
    safetyStep.row,
  );

  const verdictEl = h(
    "p",
    { class: "patrol-verdict is-idle", role: "status" },
    "Waiting for a decision",
  );
  const verdictSub = h("p", { class: "patrol-alarm-sub", hidden: true });
  const whyEl = h("p", { class: "patrol-why" }, "");
  const bigNumber = h("p", { class: "patrol-big-number" }, "—");
  const overloadLine = h("p", { class: "patrol-overload-line", hidden: true }, "");

  function metric(label: string): { box: HTMLElement; label: HTMLElement; value: HTMLElement } {
    const labelEl = h("span", { class: "patrol-metric-label" }, label);
    const value = h("span", { class: "patrol-metric-value is-empty" }, "No alarms yet");
    const box = h("div", { class: "patrol-metric" }, labelEl, value);
    return { box, label: labelEl, value };
  }
  const latencyMetric = metric("Alarm latency vs deadline");
  latencyMetric.box.title = "p99 alarm latency against the deadline";
  const onTimeMetric = metric("Alarms on time");
  const blindMetric = metric("Blind warnings on time");
  const metrics = h("div", { class: "patrol-metrics" }, latencyMetric.box, onTimeMetric.box, blindMetric.box);

  const abBody = h("tbody", null);
  const abNote = h("p", { class: "patrol-ab-note" }, "");
  const abBox = h(
    "div",
    { class: "patrol-ab-box", hidden: true },
    h(
      "h3",
      {
        class: "patrol-h",
        title:
          "Priority guard: fixed scheduling priorities for the safety core and the AI detector. No adaptive partitioning on this image.",
      },
      "Same overload, with and without our priority fix",
    ),
    h(
      "table",
      { class: "patrol-ab" },
      h("thead", null, h("tr", null, h("th", null, ""), h("th", null, "Without our fix"), h("th", null, "With our fix"))),
      abBody,
    ),
    abNote,
  );
  const positionEl = h("p", { class: "patrol-position" }, "Simulated patrol position: none yet");

  function field(label: string): { dt: HTMLElement; dd: HTMLElement } {
    const dt = h("dt", null, label);
    const dd = h("dd", null, "None yet");
    return { dt, dd };
  }

  // Two plain lines. The scheduling policy and the loop's raw numbers live in the
  // tooltip on the first line, so the rail reads as a sentence, not a register.
  const safetyLineEl = h("p", { class: "patrol-rel-line" }, "Safety core: waiting for status");
  const restartsLineEl = h("p", { class: "patrol-rel-line" }, "Restarts: none yet");
  const reliability = h("div", { class: "patrol-reliability" }, safetyLineEl, restartsLineEl);

  const underLoad = h(
    "p",
    { class: "patrol-load", hidden: true },
    "Runaway load on the Pi: the alarm path is guarded",
  );

  const killBtn = h(
    "button",
    { class: "btn patrol-kill", type: "button", hidden: true },
    "Kill the AI",
  );
  const stressBtn = h(
    "button",
    { class: "btn patrol-stress", type: "button", hidden: true },
    "Overload the Pi (10 s)",
  );
  const hint = h("p", { class: "patrol-hint", hidden: true });
  // Always offered (Kill and Overload need the Pi's controls): a visitor with no ship photo can still see an alarm.
  const recordedBtn = h(
    "button",
    { class: "btn patrol-recorded-btn", type: "button", "aria-expanded": "false" },
    "Show a recorded sighting",
  );
  const actions = h("div", { class: "patrol-actions" }, killBtn, stressBtn);
  const evidenceEl = h("p", { class: "patrol-evidence" }, "Evidence log: checking…");
  const details = h(
    "details",
    { class: "patrol-details" },
    h("summary", { class: "patrol-details-summary" }, "Details"),
    h(
      "div",
      { class: "patrol-details-body" },
      verdictSub,
      metrics,
      abBox,
      positionEl,
      chain,
      evidenceEl,
      h("h3", { class: "patrol-h" }, "Reliability"),
      reliability,
      underLoad,
    ),
  );
  const side = h(
    "aside",
    { class: "patrol-side", "aria-label": "What the patrol unit sees and decides" },
    verdictEl,
    whyEl,
    bigNumber,
    actions,
    overloadLine,
    h("div", { class: "patrol-recorded-row" }, recordedBtn),
    hint,
    details,
  );

  // ---- offline: one calm line instead of a half-live screen ----

  const offlineLine = h("p", null, "Patrol unit offline");
  const offline = h("div", { class: "patrol-offline" }, offlineLine);

  const main = h("main", { class: "main patrol-main" }, stage, side);

  app.replaceChildren(
    bar,
    guideLine(
      "patrol",
      "A camera lookout on a QNX Raspberry Pi. Hold up a ship photo, then try Kill the AI.",
    ),
    main,
  );

  function paintBadge(isOnline: boolean): void {
    if (badge === null) return;
    badge.className = `mode-badge is-${isOnline ? "live" : "error"}`;
    badge.replaceChildren(h("span", { class: "dot" }), isOnline ? " LIVE" : " OFFLINE");
  }

  /**
   * Running is neutral; restarting amber; down red. A detector that has died
   * within the last few seconds is being restarted by the supervisor, so it
   * reads "restarting" rather than a flat "down".
   */
  function detectorState(
    s: EdgeStatus,
    now: number,
  ): { label: string; tone: Tone } {
    if (s.detector_alive) {
      return {
        label: s.detector_waiting ? "waiting for frames" : "running",
        tone: s.detector_waiting ? "warn" : "ok",
      };
    }
    if (killing) return { label: "restarting", tone: "warn" };

    let newest = Number.NEGATIVE_INFINITY;
    for (const event of s.events ?? []) {
      if (!event.event.toLowerCase().includes("detector")) continue;
      const at = eventTimeMs(event);
      if (at !== null && at > newest) newest = at;
    }
    if (now - newest <= RESTART_WINDOW_MS) return { label: "restarting", tone: "warn" };
    return { label: "stopped", tone: "bad" };
  }

  function paintKill(): void {
    killBtn.hidden = !controls;
    if (!controls) return;

    if (killing) {
      killBtn.disabled = true;
      killBtn.textContent = "Killed \u2014 watching recovery\u2026";
      return;
    }

    if (recoveredUntil > Date.now() && recoveredMs !== null) {
      killBtn.disabled = true;
      killBtn.textContent = `Recovered in ${fmtRecovery(recoveredMs)}`;
      return;
    }

    killBtn.disabled = false;
    killBtn.textContent = "Kill the AI";
  }

  function paintStress(): void {
    stressBtn.hidden = !controls;
    if (!controls) return;
    stressBtn.disabled = stressing || stressActive;
    stressBtn.textContent = stressing ? "Overloading the Pi\u2026" : "Overload the Pi (10 s)";
  }

  /** The one number: the live alarm latency against the deadline, or a dash until a live alarm lands. */
  function paintBigNumber(s: EdgeStatus): void {
    const a = s.alarm;
    const p99 = finiteOrNull(a?.p99_latency_ms);
    if (!a || a.decisions === 0 || p99 === null) {
      bigNumber.textContent = "—";
      bigNumber.classList.remove("is-bad");
      return;
    }
    bigNumber.textContent = `Alarm in ${Math.round(p99)} ms · deadline ${a.deadline_ms} ms`;
    bigNumber.classList.toggle("is-bad", p99 > a.deadline_ms);
  }

  /** The one overload line: the same two arms the A/B table shows, read from the same result. */
  function paintOverloadLine(result: EdgeAb | null): void {
    overloadLine.hidden = result === null;
    if (result === null) return;
    const g = result.guarded;
    const u = result.unguarded;
    overloadLine.textContent =
      `Under overload: ${fmtInt(g.on_time)} of ${fmtInt(g.alarm_frames)} alarms on time with our fix, ` +
      `${fmtInt(u.on_time)} of ${fmtInt(u.alarm_frames)} without.`;
  }

  /** "Evidence log: N sightings · chain verified", from the hash-chained log (#220). */
  function paintEvidence(payload: SightingsPayload | null): void {
    lastSightings = payload;
    if (recordedShown()) {
      evidenceEl.textContent = "Example only, not written to the log";
      return;
    }
    if (payload === null) {
      evidenceEl.textContent = "Evidence log: no sightings yet";
      return;
    }
    const count = payload.chain?.records ?? payload.sightings.length;
    const chain = payload.chain?.ok === true ? "chain verified" : "chain not verified";
    evidenceEl.textContent = `Evidence log: ${count} sightings · ${chain}`;
  }

  /** Two reliability lines, the scheduling detail on hover, then stress and controls. */
  function paintReliability(s: EdgeStatus): void {
    const now = Date.now();
    const priorities = s.priorities ?? {};
    safetyLineEl.textContent = safetyLine(s);
    safetyLineEl.title = [
      `Safety core ${schedValue(priorities.safety_core)}`,
      `AI detector ${schedValue(priorities.detector)}`,
      loopDetail(s.safety_tick),
    ].join(" · ");
    restartsLineEl.textContent = `Restarts: ${fmtInt(s.detector_restarts ?? 0)}`;

    stressActive = s.stress?.active === true;
    underLoad.hidden = !stressActive;

    controls = s.controls === true;

    // The kill is finished the first time the detector is seen alive again
    // after it was seen dead. The 8 s guard covers a kill that lands between
    // two polls (the supervisor restarts within ~2 s), so the button cannot
    // stay on "watching recovery" forever.
    lastRestarts = finiteOrNull(s.detector_restarts) ?? lastRestarts;
    const measured = finiteOrNull(s.last_recovery_ms);
    const restartedSinceKill =
      restartsAtKill !== null && lastRestarts > restartsAtKill && measured !== recoveryAtKill;
    if (
      killing &&
      s.detector_alive &&
      // A warm standby recovers in ~150 ms; hold "restarting" long enough to be read.
      (killedAt === null || now - killedAt >= KILL_MIN_SHOW_MS) &&
      ((restartedSinceKill && measured !== null) ||
        (killedAt !== null && now - killedAt >= 8000))
    ) {
      // Show the supervisor's measured number, never a browser-side timing; the
      // 8 s guard only stops the button from waiting forever.
      recoveredMs = restartedSinceKill ? measured : null;
      restartsAtKill = null;
      recoveredUntil = now + RECOVERY_HOLD_MS;
      killing = false;
      killedAt = null;
    }

    paintKill();
    paintStress();
  }

  function paintCaption(d: EdgeDecision | null): void {
    if (d === null) {
      caption.textContent = "Waiting for the first decision";
      return;
    }
    const parts: string[] = [`Frame ${String(d.frame_id)}`];
    const at = wallMs(d.t);
    if (at !== null) parts.push(fmtWall(at));
    const inference = finiteOrNull(d.ai?.inference_ms);
    if (inference !== null) parts.push(`AI ${Math.round(inference)} ms`);
    caption.textContent = parts.join(" \u00b7 ");
  }

  /** The three steps, each one line: what saw, what guessed, what checked AIS. */
  function paintChain(s: EdgeStatus, d: EdgeDecision | null): void {
    // The recorded example owns the chain while it is open: live numbers would contradict it.
    if (recordedShown()) {
      setStep(cameraStep, "idle", `frame ${RECORDED_SIGHTING.frame} (recorded)`);
      setStep(aiStep, "ok", `${RECORDED_SIGHTING.label} ${RECORDED_SIGHTING.scorePct}% (recorded)`);
      setStep(safetyStep, "bad", "No ship broadcasting within 2 km \u2192 alert (recorded)");
      return;
    }

    const detector = detectorState(s, Date.now());

    // 1 Camera: the frame rate the safety core processed frames at.
    const fps = finiteOrNull(s.frames_per_s);
    if (cameraOffline) {
      setStep(cameraStep, "bad", "offline");
    } else if (!s.frame_available) {
      setStep(cameraStep, "warn", "waiting for frames");
    } else if (fps !== null) {
      const unit = fps === 1 ? "frame" : "frames";
      setStep(cameraStep, "ok", `${fmtRate(fps)} ${unit} a second`);
    } else {
      setStep(cameraStep, "ok", "frames arriving");
    }

    // 2 AI: untrusted, and named as such; under stress its slower time is the point.
    if (killing || !s.detector_alive || d?.ai?.down === true) {
      const label = s.detector_alive || killing ? "restarting" : detector.label;
      setStep(aiStep, s.detector_alive || killing ? "warn" : detector.tone, label);
    } else if (s.detector_waiting) {
      setStep(aiStep, "warn", "waiting for frames");
    } else if (d === null) {
      setStep(aiStep, "idle", "waiting for a decision");
    } else {
      let text = aiText(d.ai);
      if (stressActive) {
        const inference = finiteOrNull(d.ai?.inference_ms);
        if (inference !== null) text += ` \u00b7 ${Math.round(inference)} ms`;
      }
      setStep(aiStep, "ok", text);
    }

    // 3 Safety core: the trusted AIS check, independent of the AI's opinion.
    const safetyTone: Tone =
      s.state === "OK" ? "ok" : s.state === "DEGRADED" ? "warn" : "idle";
    setStep(safetyStep, safetyTone, aisLine(d));
  }

  function paintVerdict(d: EdgeDecision | null): void {
    // While the recorded example is open the banner speaks for it too, so the screen never
    // says "alert" on the left and "no vessel in view" on the right.
    const b = recordedShown()
      ? { level: "alert" as const, text: "Vessel not broadcasting its position", sub: "recorded example · not live" }
      : banner(status, d);
    const key = `${b.level}|${b.text}`;
    verdictSub.textContent = b.sub;
    verdictSub.hidden = b.sub === "";
    whyEl.textContent = whyLine(b.level, d, recordedShown());
    if (key === lastVerdictKey) return;

    const first = lastVerdictKey === null;
    lastVerdictKey = key;
    verdictEl.className = `patrol-verdict is-${b.level}`;
    verdictEl.textContent = b.text;

    // Motion only to explain: a fade marks a new state, nothing idles.
    if (!first && !noMotion.matches) {
      verdictEl.classList.remove("is-fading");
      void verdictEl.offsetWidth;
      verdictEl.classList.add("is-fading");
    }
  }

  /**
   * Live guarantee figures: latency against the deadline, alarms on time, blind raised on time.
   * Hidden until a live alarm lands; before that the A/B card right below carries the measured
   * proof, and repeating its numbers here would say the same thing twice.
   */
  function paintMetrics(s: EdgeStatus, _result: EdgeAb | null): void {
    const a = s.alarm;
    metrics.hidden = !a || a.decisions === 0;
    if (!a || a.decisions === 0) return;

    latencyMetric.label.textContent = "Alarm latency vs deadline";
    onTimeMetric.label.textContent = "Alarms on time";
    blindMetric.label.textContent = "Blind warnings on time";
    latencyMetric.box.title = "p99 alarm latency against the deadline";
    onTimeMetric.box.title = "";
    blindMetric.box.title = "";
    for (const m of [latencyMetric, onTimeMetric, blindMetric]) {
      m.value.classList.remove("is-empty");
    }
    const p99 = finiteOrNull(a.p99_latency_ms);
    latencyMetric.value.textContent =
      p99 === null ? `none yet / ${a.deadline_ms} ms` : `${Math.round(p99)} / ${a.deadline_ms} ms`;
    latencyMetric.box.classList.toggle("is-bad", p99 !== null && p99 > a.deadline_ms);
    onTimeMetric.value.textContent = `${fmtInt(a.on_time)}/${fmtInt(a.decisions)}`;
    onTimeMetric.box.classList.toggle("is-bad", a.on_time < a.decisions);
    if (a.blind_episodes === 0) {
      blindMetric.value.textContent = "None yet";
      blindMetric.value.classList.add("is-empty");
      blindMetric.box.classList.remove("is-bad");
      return;
    }
    blindMetric.value.textContent = `${fmtInt(a.blind_on_time)}/${fmtInt(a.blind_episodes)}`;
    blindMetric.box.classList.toggle("is-bad", a.blind_on_time < a.blind_episodes);
  }

  function armCells(arm: EdgeAbArm): string[] {
    const worst = finiteOrNull(arm.worst_latency_ms);
    return [
      `${fmtInt(arm.on_time)}/${fmtInt(arm.alarm_frames)}`,
      worst === null ? "none yet" : `${Math.round(worst)} ms`,
      `${fmtMs(finiteOrNull(arm.worst_tick_late_ms) ?? 0)} ms`,
    ];
  }

  /** The recorded result of the priority-guard test; nothing is shown until one has been run. */
  function paintAb(result: EdgeAb | null): void {
    abBox.hidden = result === null;
    if (result === null) return;
    const off = armCells(result.unguarded);
    const on = armCells(result.guarded);
    abBody.replaceChildren(
      ...AB_ROWS.map((row, k) =>
        h(
          "tr",
          null,
          h("th", { scope: "row", title: row.title }, row.label),
          // Only the first row is coloured: "alarms on time" is the claim.
          h("td", { class: k === 0 && result.unguarded.late > 0 ? "is-bad" : "" }, off[k] ?? ""),
          h("td", null, on[k] ?? ""),
        ),
      ),
    );
    abNote.textContent = `Each column counts the alarms raised in its own ${fmtInt(result.stress_s)} s overload run.`;
    abNote.title = `Measured on the Pi with all four cores overloaded for ${fmtInt(result.stress_s)} s. Deadline: ${result.deadline_ms} ms. Load: ${result.load}.`;
  }

  /** "Simulated patrol position: Kiel Bight (54.45, 10.25)": the unit's own name and fix. */
  function paintPosition(d: EdgeDecision | null): void {
    const name =
      typeof d?.patrol?.name === "string" && d.patrol.name.trim() !== ""
        ? d.patrol.name.trim()
        : null;
    const lat = finiteOrNull(d?.patrol?.lat);
    const lon = finiteOrNull(d?.patrol?.lon);
    const coords = lat !== null && lon !== null ? `(${lat.toFixed(2)}, ${lon.toFixed(2)})` : null;
    const body = [name, coords].filter((part): part is string => part !== null).join(" ");
    positionEl.textContent = `Simulated patrol position: ${body !== "" ? body : "none yet"}`;
  }

  function paint(): void {
    if (status === null) return;
    paintMetrics(status, ab);
    cameraOffline = isCameraOffline(status, decision);
    syncFrame();
    paintReliability(status);
    paintBigNumber(status);
    paintOverloadLine(ab);
    paintCaption(decision);
    paintChain(status, decision);
    paintVerdict(decision);
    paintPosition(decision);
    paintAb(ab);

    if (decision !== null) {
      const id = finiteOrNull(decision.frame_id);
      // Frame id missing (older writer): paint the first frame, then wait for a
      // real id instead of hammering the camera every second.
      const nextId = id === null ? -1 : id;
      if (nextId !== lastFrameId) {
        lastFrameId = nextId;
        loadFrame();
      }
    }
  }

  function showOffline(): void {
    online = false;
    syncFrame();
    paintBadge(false);
    const seen =
      lastOkMs === null ? null : Math.max(1, Math.floor((Date.now() - lastOkMs) / 1000));
    offlineLine.textContent =
      seen === null ? "Patrol unit offline" : `Patrol unit offline \u00b7 last seen ${seen} s ago`;
    if (main.firstElementChild !== offline) main.replaceChildren(offline);
  }

  async function poll(): Promise<void> {
    if (disposed || pollInFlight) return;
    if (!main.isConnected) {
      dispose();
      return;
    }

    pollInFlight = true;
    try {
      // Status is the unit's heartbeat; the decision is what it concluded. A
      // missing decision is not an offline unit, so the two are judged apart.
      // Status first: asking an offline unit for a decision only fills the console with 404s.
      const [statusResult] = await Promise.allSettled([fetchEdgeStatus()]);
      if (disposed) return;

      if (statusResult.status === "rejected") {
        showOffline();
        return;
      }
      const next = statusResult.value;
      // A 200 with no status file is the API saying "no unit"; that is offline,
      // not a screen full of zeros.
      if (next.state === "UNKNOWN" && next.error) {
        showOffline();
        return;
      }

      const [decisionResult] = await Promise.allSettled([fetchEdgeDecision()]);
      if (disposed) return;
      // The A/B result changes only when someone runs it: look every 30 s, and only until found.
      if (Date.now() - abPolledAt > 30000) {
        abPolledAt = Date.now();
        const [abResult] = await Promise.allSettled([fetchEdgeAb()]);
        if (disposed) return;
        ab = abResult.status === "fulfilled" ? abResult.value : ab;
      }

      lastOkMs = Date.now();
      if (!online) {
        online = true;
        main.replaceChildren(stage, side);
        syncFrame();
      }
      paintBadge(true);

      status = next;
      if (decisionResult.status === "fulfilled") decision = decisionResult.value;
      paint();
    } catch {
      if (!disposed) showOffline();
    } finally {
      pollInFlight = false;
    }
  }

  async function requestKill(): Promise<void> {
    if (disposed || killing) return;

    killing = true;
    killedAt = Date.now();
    restartsAtKill = lastRestarts;
    recoveryAtKill = finiteOrNull(status?.last_recovery_ms);
    recoveredUntil = 0;
    hint.hidden = true;
    hint.textContent = "";
    paintKill();

    try {
      await killEdgeDetector();
      if (disposed) return;
      // Don't wait up to a second to show "restarting".
      void poll();
    } catch {
      if (disposed) return;
      killing = false;
      killedAt = null;
      paintKill();
      hint.textContent = "The patrol unit did not accept the kill request.";
      hint.hidden = false;
    }
  }

  killBtn.addEventListener("click", () => {
    void requestKill();
  });

  recordedBtn.addEventListener("click", () => {
    const open = recordedPanel.hidden;
    recordedPanel.hidden = !open;
    recordedBtn.setAttribute("aria-expanded", String(open));
    recordedBtn.textContent = open ? "Hide the recorded sighting" : "Show a recorded sighting";
    // Repaint at once: a chain of live numbers under a recorded panel would contradict it.
    if (status !== null) paintChain(status, decision);
    paintVerdict(decision);
    paintEvidence(lastSightings);
  });

  async function requestStress(): Promise<void> {
    if (disposed || stressing) return;

    stressing = true;
    hint.hidden = true;
    hint.textContent = "";
    paintStress();

    try {
      await stressEdgeDetector();
      if (disposed) return;
      // Don't wait up to a second for the amber "AI under load" line.
      void poll();
    } catch {
      if (disposed) return;
      hint.textContent = "The patrol unit did not accept the stress request.";
      hint.hidden = false;
    } finally {
      if (!disposed) {
        stressing = false;
        paintStress();
      }
    }
  }

  stressBtn.addEventListener("click", () => {
    void requestStress();
  });

  // ---- timer: visible tab only ----

  /**
   * The evidence log is re-hashed on the Pi for every read, so it has its own slow
   * timer and is never awaited inside the frame/decision loop.
   */
  async function pollSightings(): Promise<void> {
    if (disposed || sightingsInFlight) return;
    sightingsInFlight = true;
    try {
      const payload = await fetchSightings();
      if (!disposed) paintEvidence(payload);
    } finally {
      sightingsInFlight = false;
    }
  }

  function startTimer(): void {
    if (disposed || pollTimer !== null) return;
    pollTimer = window.setInterval(() => {
      void poll();
    }, POLL_MS);
    sightingsTimer = window.setInterval(() => {
      void pollSightings();
    }, SIGHTINGS_MS);
    void pollSightings();
  }

  function stopTimer(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    if (sightingsTimer !== null) {
      window.clearInterval(sightingsTimer);
      sightingsTimer = null;
    }
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    stopTimer();
    document.removeEventListener("visibilitychange", onVisibility);
  }

  function onVisibility(): void {
    if (disposed) return;
    if (document.visibilityState === "visible") {
      startTimer();
      void poll();
    } else {
      stopTimer();
    }
  }

  document.addEventListener("visibilitychange", onVisibility);
  if (document.visibilityState === "visible") {
    startTimer();
    void poll();
  }
}
