/**
 * Patrol unit tab (#36): is the unit still safe when its AI fails?
 *
 * Left: the camera frame, annotated by the untrusted detector, refreshed at
 * ~4 fps while the tab is visible. Right: the one line that matters (the
 * safety core is running) and the detector's state, then the numbers, the
 * scheduling classes and deadline statistics that make the real-time claim
 * checkable, the last six supervisor events, and the two demo actions: kill
 * the detector, or load every core at the detector's priority.
 *
 * The screen owns its timers because the router does not hold a handle to it.
 * Every tick checks `main.isConnected`; the moment another tab replaces this
 * screen the intervals stop, and nothing is fetched in a hidden tab.
 */

import "../styles/patrol.css";

import type { EdgeEvent, EdgeSchedule, EdgeStatus } from "../api";
import { edgeFrameUrl, fetchEdgeStatus, killEdgeDetector, stressEdgeDetector } from "../api";
import { h } from "../components/dom";
import { topBar } from "../components/TopBar";
import { fmtInt, fmtUtcFull } from "../format";

/** Status poll interval (only while the document is visible). */
const POLL_MS = 1000;
/** 4 fps: fast enough to read as live, slow enough that the image settles. */
const FRAME_MS = 250;
/** How long the measured recovery number stays on the button after recovery. */
const RECOVERY_HOLD_MS = 5000;
/** A detector event younger than this means the supervisor is mid-restart. */
const RESTART_WINDOW_MS = 5000;
/** Calm list length: never turn the rail into a log viewer. */
const EVENTS_SHOWN = 6;

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

/** "just now" / "12 s ago" / "3 min ago" / "2 h ago"; never a future time. */
function relativeTime(ms: number, now: number): string {
  const diff = Math.max(0, now - ms);
  if (diff < 5000) return "just now";
  if (diff < 60_000) return `${Math.floor(diff / 1000)} s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
  return `${Math.floor(diff / 3_600_000)} h ago`;
}

/** Supervisor event names are terse; the screen uses plain words for the known ones. */
function eventText(event: EdgeEvent): string {
  switch (event.event) {
    case "detector started":
      return "AI detector started";
    case "detector down":
      return "AI detector stopped";
    case "detector silent, killing":
      return "AI detector stopped responding";
    case "detector recovered":
      return typeof event.recovery_ms === "number"
        ? `AI detector recovered in ${Math.round(event.recovery_ms)} ms`
        : "AI detector recovered";
    case "safety_core started":
      return "Safety core started";
    case "safety_core exited":
      return "Safety core restarted";
    default:
      return event.event.replace(/_/g, " ");
  }
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function msLabel(value: number | null | undefined): string {
  const ms = finiteOrNull(value);
  return ms === null ? "\u2014" : `${Math.round(ms)} ms`;
}

/**
 * "Safety core: SCHED_FIFO 30" on a board that granted it, "AI detector: nice +10"
 * when it did not. The policy comes from the supervisor, never from a guess here.
 */
function schedLabel(name: string, sched: EdgeSchedule | null | undefined): string {
  if (!sched) return `${name}: \u2014`;
  const policy = (sched.policy ?? "").toUpperCase();
  const priority = finiteOrNull(sched.priority) ?? 0;
  if (policy === "SCHED_FIFO" || policy === "SCHED_RR") {
    return `${name}: ${policy} ${priority}`;
  }
  const nice = finiteOrNull(sched.nice) ?? 0;
  return `${name}: nice ${nice >= 0 ? "+" : ""}${nice}`;
}

/**
 * Paint the Patrol screen into `app`.
 *
 * Returns synchronously. A status fetch continues in the background; the
 * intervals stop themselves when the screen is replaced or the tab is hidden.
 */
export function renderPatrol(app: HTMLElement): void {
  let disposed = false;
  /** True between a successful status fetch and the next failure. */
  let online = false;
  /** One status request at a time: a slow API must not queue a second poll. */
  let statusInFlight = false;
  let pollTimer: number | null = null;
  let frameTimer: number | null = null;
  let lastOkMs: number | null = null;

  // Frame state. The visible <img> is only swapped once the next frame has
  // loaded, so 4 fps never flashes the black stage between frames.
  let frameAvailable = false;
  let frameLoaded = false;

  // Kill/recovery state.
  let controls = false;
  let killing = false;
  let stressing = false;
  let stressActive = false;
  let sawDead = false;
  let killedAt: number | null = null;
  let recoveredMs: number | null = null;
  // Restart count when the kill was requested: the supervisor's own recovery_ms is
  // only final once a restart past this count has delivered its first frame.
  let restartsAtKill: number | null = null;
  let lastRestarts = 0;
  let recoveredUntil = 0;

  const bar = topBar({
    active: "patrol",
    mode: "LIVE",
    generatedAt: null,
    caseHref: null,
    demoHref: null,
  });
  const badge = bar.querySelector<HTMLElement>(".mode-badge");

  // ---- stage: the annotated camera frame ----

  const frameImg = h("img", {
    class: "patrol-frame-img",
    alt: "Camera frame from the patrol unit, with the AI detector's boxes drawn on it",
    hidden: true,
  });
  const framePlaceholder = h(
    "div",
    { class: "patrol-frame-empty" },
    "Waiting for the camera\u2026",
  );

  /** The offscreen loader; a decoded frame replaces the visible one atomically. */
  const frameBuffer = new Image();

  function syncFrame(): void {
    const show = online && frameAvailable && frameLoaded;
    frameImg.hidden = !show;
    framePlaceholder.hidden = show;
  }

  frameBuffer.onload = (): void => {
    if (disposed) return;
    frameLoaded = true;
    frameImg.src = frameBuffer.src;
    syncFrame();
  };
  frameBuffer.onerror = (): void => {
    if (disposed) return;
    frameLoaded = false;
    syncFrame();
  };

  const stage = h(
    "section",
    { class: "stage patrol-stage" },
    h("div", { class: "patrol-frame" }, frameImg, framePlaceholder),
  );

  // ---- rail: state, numbers, events, action ----

  const safetyValue = h("span", { class: "patrol-state-value" }, "checking\u2026");
  const safetyLine = h(
    "div",
    { class: "patrol-state" },
    h("span", { class: "patrol-state-name" }, "Safety core:"),
    safetyValue,
  );

  const detectorValue = h(
    "span",
    { class: "patrol-state-value", "aria-live": "polite" },
    "checking\u2026",
  );
  const detectorLine = h(
    "div",
    { class: "patrol-state" },
    h("span", { class: "patrol-state-name" }, "AI detector:"),
    detectorValue,
  );

  const states = h("div", { class: "patrol-states" }, safetyLine, detectorLine);

  function metric(label: string): { row: HTMLElement; value: HTMLElement } {
    const value = h("span", { class: "patrol-metric-value" }, "\u2014");
    const row = h(
      "div",
      { class: "patrol-metric" },
      h("span", { class: "patrol-metric-label" }, label),
      value,
    );
    return { row, value };
  }

  const frames = metric("Frames");
  const inference = metric("Inference");
  const misses = metric("Deadline misses");
  const restarts = metric("Detector restarts");
  const recovery = metric("Last recovery");
  const alerts = metric("Alerts");

  const metrics = h(
    "div",
    { class: "patrol-metrics" },
    frames.row,
    inference.row,
    misses.row,
    restarts.row,
    recovery.row,
    alerts.row,
  );

  const eventsList = h("ol", { class: "patrol-events" });
  const eventsEmpty = h("p", { class: "patrol-events-empty" }, "No events yet.");

  // ---- real-time detail: scheduling classes, latency, deadline misses ----

  const safetySched = h("div", { class: "patrol-line" }, "Safety core: \u2014");
  const detectorSched = h("div", { class: "patrol-line" }, "AI detector: \u2014");
  const checkP99 = h("div", { class: "patrol-line" }, "Safety check p99: \u2014");
  const misses60 = h("div", { class: "patrol-line" }, "Deadline misses (60 s): \u2014");
  const underLoad = h("div", { class: "patrol-line is-amber", hidden: true }, "AI under load");

  const detail = h(
    "div",
    { class: "patrol-detail" },
    safetySched,
    detectorSched,
    checkP99,
    misses60,
    underLoad,
  );

  const killBtn = h(
    "button",
    { class: "btn patrol-kill", type: "button", hidden: true },
    "Kill the AI detector",
  );
  const stressBtn = h(
    "button",
    { class: "btn patrol-stress", type: "button", hidden: true },
    "Stress the AI (10 s)",
  );
  const actions = h("div", { class: "patrol-actions" }, killBtn, stressBtn);
  const killHint = h("p", { class: "patrol-hint", hidden: true });
  const killNote = h(
    "p",
    { class: "patrol-note" },
    "The detector is untrusted. A small safety core keeps checking for ships that don't broadcast, even while the AI restarts.",
  );
  const runsOn = h("p", { class: "patrol-source" }, "Runs on: laptop");

  const rail = h(
    "aside",
    { class: "patrol-rail" },
    h("h2", { class: "patrol-title" }, "Patrol unit"),
    states,
    metrics,
    detail,
    h("h3", { class: "patrol-h" }, "Recent events"),
    eventsList,
    eventsEmpty,
    h("div", { class: "patrol-foot" }, actions, killHint, killNote, runsOn),
  );

  // ---- offline: one calm block instead of a half-live screen ----

  const offlineLast = h("p", null, "Last seen: never");
  const offline = h(
    "div",
    { class: "patrol-offline" },
    h("h2", null, "Patrol unit offline"),
    offlineLast,
  );

  const main = h("main", { class: "main patrol-main" }, stage, rail);

  app.replaceChildren(bar, main);

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
    status: EdgeStatus,
    now: number,
  ): { label: string; tone: "running" | "restarting" | "down" } {
    if (status.detector_alive) {
      // Alive but the source has no frame yet: honest, and still the quiet baseline.
      return {
        label: status.detector_waiting ? "waiting for frames" : "running",
        tone: "running",
      };
    }
    if (killing) return { label: "restarting", tone: "restarting" };

    let newest = Number.NEGATIVE_INFINITY;
    for (const event of status.events ?? []) {
      if (!event.event.toLowerCase().includes("detector")) continue;
      const at = eventTimeMs(event);
      if (at !== null && at > newest) newest = at;
    }
    if (now - newest <= RESTART_WINDOW_MS) return { label: "restarting", tone: "restarting" };
    return { label: "down", tone: "down" };
  }

  function paintMetrics(status: EdgeStatus): void {
    frames.value.textContent = fmtInt(status.frames_total ?? 0);
    inference.value.textContent = msLabel(status.last_inference_ms);
    misses.value.textContent = fmtInt(status.deadline_misses ?? 0);
    restarts.value.textContent = fmtInt(status.detector_restarts ?? 0);
    recovery.value.textContent = msLabel(status.last_recovery_ms);
    alerts.value.textContent = fmtInt(status.alerts_total ?? 0);
  }

  function paintDetail(status: EdgeStatus): void {
    const priorities = status.priorities ?? {};
    safetySched.textContent = schedLabel("Safety core", priorities.safety_core);
    detectorSched.textContent = schedLabel("AI detector", priorities.detector);
    checkP99.textContent = `Safety check p99: ${msLabel(status.latency_p99_ms)}`;
    misses60.textContent = `Deadline misses (60 s): ${fmtInt(status.deadline_misses_60s ?? 0)}`;
    stressActive = status.stress?.active === true;
    underLoad.hidden = !stressActive;
  }

  function paintEvents(events: EdgeEvent[]): void {
    // Newest first, and never more than a glance: six rows, calm list.
    const recent = events.slice(-EVENTS_SHOWN).reverse();
    const now = Date.now();
    eventsList.replaceChildren();
    eventsList.hidden = recent.length === 0;
    eventsEmpty.hidden = recent.length > 0;

    for (const event of recent) {
      const at = eventTimeMs(event);
      eventsList.append(
        h(
          "li",
          { class: "patrol-event" },
          h(
            "span",
            { class: "patrol-event-time" },
            at === null ? "\u2014" : relativeTime(at, now),
          ),
          h("span", { class: "patrol-event-text" }, eventText(event)),
        ),
      );
    }
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
      killBtn.textContent = `Recovered in ${Math.round(recoveredMs)} ms`;
      return;
    }

    killBtn.disabled = false;
    killBtn.textContent = "Kill the AI detector";
  }

  function paintStress(): void {
    stressBtn.hidden = !controls;
    if (!controls) return;
    stressBtn.disabled = stressing || stressActive;
    stressBtn.textContent = stressing ? "Stressing the AI\u2026" : "Stress the AI (10 s)";
  }

  function paint(status: EdgeStatus): void {
    online = true;
    if (main.firstElementChild !== stage) main.replaceChildren(stage, rail);
    paintBadge(true);

    controls = status.controls === true;
    safetyValue.textContent = "running";

    const hadFrame = frameAvailable;
    frameAvailable = status.frame_available === true;
    if (frameAvailable && !hadFrame) {
      // Paint the first frame now; the visible image is swapped on load, so
      // this never shows a half-rendered JPEG.
      frameBuffer.src = edgeFrameUrl(Date.now());
    }
    syncFrame();

    const now = Date.now();
    if (!status.detector_alive) sawDead = true;

    // The kill is finished the first time the detector is seen alive again
    // after it was seen dead. The 3 s guard covers a kill that lands between
    // two polls (the supervisor restarts within ~2 s), so the button cannot
    // stay on "watching recovery" forever.
    lastRestarts = finiteOrNull(status.detector_restarts) ?? lastRestarts;
    const measured = finiteOrNull(status.last_recovery_ms);
    const restartedSinceKill = restartsAtKill !== null && lastRestarts > restartsAtKill;
    if (
      killing &&
      status.detector_alive &&
      ((restartedSinceKill && measured !== null) ||
        (killedAt !== null && now - killedAt >= 8000))
    ) {
      // Show the supervisor's measured number (the one the panel shows), never a
      // browser-side timing; the 8 s guard only stops the button waiting forever.
      recoveredMs = restartedSinceKill ? measured : null;
      restartsAtKill = null;
      recoveredUntil = now + RECOVERY_HOLD_MS;
      killing = false;
      sawDead = false;
      killedAt = null;
    }
    if (!killing) sawDead = false;

    const state = detectorState(status, now);
    detectorValue.textContent = state.label;
    detectorValue.className = `patrol-state-value is-${state.tone}`;

    paintMetrics(status);
    paintDetail(status);
    paintEvents(status.events ?? []);
    paintKill();
    paintStress();

    runsOn.textContent =
      status.source === "pi" ? "Runs on: QNX Raspberry Pi" : "Runs on: laptop";
  }

  function showOffline(): void {
    online = false;
    frameAvailable = false;
    syncFrame();
    paintBadge(false);
    offlineLast.textContent =
      lastOkMs === null
        ? "Last seen: never"
        : `Last seen ${fmtUtcFull(new Date(lastOkMs).toISOString())}`;
    if (main.firstElementChild !== offline) main.replaceChildren(offline);
  }

  async function poll(): Promise<void> {
    if (disposed || statusInFlight) return;
    if (!main.isConnected) {
      dispose();
      return;
    }

    statusInFlight = true;
    try {
      const status = await fetchEdgeStatus();
      if (disposed) return;
      // A 200 with no status file is the API saying "no unit"; that is offline,
      // not a screen full of zeros.
      if (status.state === "UNKNOWN" && status.error) {
        showOffline();
        return;
      }
      lastOkMs = Date.now();
      paint(status);
    } catch {
      if (!disposed) showOffline();
    } finally {
      statusInFlight = false;
    }
  }

  function frameTick(): void {
    if (disposed) return;
    if (!main.isConnected) {
      dispose();
      return;
    }
    if (!online || !frameAvailable || document.visibilityState !== "visible") return;
    frameBuffer.src = edgeFrameUrl(Date.now());
  }

  async function requestKill(): Promise<void> {
    if (disposed || killing) return;

    killing = true;
    sawDead = false;
    killedAt = Date.now();
    restartsAtKill = lastRestarts;
    recoveredUntil = 0;
    killHint.hidden = true;
    killHint.textContent = "";
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
      killHint.textContent = "The patrol unit did not accept the kill request.";
      killHint.hidden = false;
    }
  }

  killBtn.addEventListener("click", () => {
    void requestKill();
  });

  async function requestStress(): Promise<void> {
    if (disposed || stressing) return;

    stressing = true;
    killHint.hidden = true;
    killHint.textContent = "";
    paintStress();

    try {
      await stressEdgeDetector();
      if (disposed) return;
      // Don't wait up to a second for the amber "AI under load" line.
      void poll();
    } catch {
      if (disposed) return;
      killHint.textContent = "The patrol unit did not accept the stress request.";
      killHint.hidden = false;
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

  // ---- timers: visible tab only ----

  function startTimers(): void {
    if (disposed) return;
    if (pollTimer === null) {
      pollTimer = window.setInterval(() => {
        void poll();
      }, POLL_MS);
    }
    if (frameTimer === null) {
      frameTimer = window.setInterval(frameTick, FRAME_MS);
    }
  }

  function stopTimers(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    if (frameTimer !== null) {
      window.clearInterval(frameTimer);
      frameTimer = null;
    }
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    stopTimers();
    document.removeEventListener("visibilitychange", onVisibility);
  }

  function onVisibility(): void {
    if (disposed) return;
    if (document.visibilityState === "visible") {
      startTimers();
      void poll();
    } else {
      stopTimers();
    }
  }

  document.addEventListener("visibilitychange", onVisibility);
  if (document.visibilityState === "visible") {
    startTimers();
    void poll();
  }
}
