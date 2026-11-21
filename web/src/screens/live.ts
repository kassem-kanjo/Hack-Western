/**
 * Live screen: what is happening in one region right now.
 *
 * Left stage: the dim AIS field with event markers, the one selected event and
 * a tick strip for the last window. Right rail: either the ranked list with its
 * kind filter, or the proof for the selected event.
 *
 * All maths and wording lives in model/liveFacts.ts; this file only builds the
 * DOM and wires cursor moves. The URL is the single source of truth for the
 * selection: clicks assign location.hash and the router calls select(), so a
 * reload always reproduces the same view and the map never disagrees with the
 * address bar.
 *
 * Replay (#85) is time travel V1: the strip's axis becomes a scrubber over the
 * recorded window and `t` gates the events the map, the hero number, the kind
 * chips and the rail show. The snapshot has event start/end times and vessel
 * last positions only, so replay moves the findings clock, not the ships: no
 * invented track between fixes. Leaving replay restores the live view from the
 * same data.
 */

import "../styles/live.css";

import { fetchLiveEvents, fetchLiveRegions, fetchLiveSummary, fetchLiveVessels } from "../api";
import { eventDetail } from "../components/EventDetail";
import { REPLAY_SPEEDS, replayBar, replayDurationMs } from "../components/ReplayBar";
import type { ReplaySpeed } from "../components/ReplayBar";
import { h } from "../components/dom";
import { topBar } from "../components/TopBar";
import { vesselCard } from "../components/VesselCard";
import { fmtUtc } from "../format";
import { mountLiveMap } from "../map/liveMap";
import type { EventKind, LiveEvent, LiveRegion, LiveSummary, LiveVessel } from "../model/live";
import { EVENT_KINDS } from "../model/live";
import {
  KIND_LABEL,
  eventsOfKinds,
  fmtCoverage,
  kindCounts,
  rankRows,
  snapshotNow,
  sortEvents,
  timeAgo,
  timelineFraction,
  unusualCount,
  vesselName,
  watchlistForVessel,
  watchlistHit,
  windowHours,
} from "../model/liveFacts";

/** The three places the switcher offers, in the order the design lists them. */
const REGIONS: readonly { id: string; label: string }[] = [
  { id: "BALTIC_WATCH", label: "Baltic" },
  { id: "MALAYSIA_EOPL", label: "Malaysia EOPL" },
  { id: "BLACK_SEA", label: "Black Sea" },
];

/**
 * Marker cap for the map only. Past ~150 markers overlapping icons hide the
 * density field they are drawn over; the rail and the strip keep every event.
 */
const MAP_EVENT_CAP = 150;

/** Rows the rail can carry without turning into a database. */
const LIST_ROWS = 7;

/** The API's documented maximum; ask for it so the list is never silently short. */
const EVENT_LIMIT = 500;

/** Replay's arrow-key step. Ten minutes is one readable notch on a 24 h window. */
const REPLAY_STEP_MS = 10 * 60_000;

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Everything a live hash can ask for. The three targets are mutually
 * exclusive; an event index is a position in the severity-sorted list, while
 * `kind` and `vessel` are resolved here after loading so the route itself never
 * depends on the order the API happened to return.
 */
export interface LiveRoute {
  region: string;
  eventIndex: number | null;
  /** Open the highest-severity finding of this kind. */
  kind?: string | null;
  /** Open this vessel's card. */
  vessel?: number | null;
}

export interface LiveScreen {
  destroy(): void;
  /** Called by the router when only the event part of the hash changed. Must NOT re-fetch or rebuild the map. */
  select(index: number | null): void;
  /** Open the highest-severity finding of a kind, without rewriting the route. */
  selectKind(kind: string): void;
  /** Open one vessel's card, without rewriting the route. */
  selectVessel(mmsi: number): void;
}

/** True only for the five kinds the map and the chips understand. */
function isEventKind(value: string): value is EventKind {
  return (EVENT_KINDS as readonly string[]).includes(value);
}

/**
 * Parse one live hash. This mirrors the router's contract and lets a same-region
 * navigation keep the screen alive when only the kind or the vessel changed.
 */
export function parseLiveRoute(hash: string): LiveRoute | null {
  const path = hash.startsWith("#") ? hash.slice(1) : hash;
  const match = /^\/live\/([A-Z_]+)(?:\/event\/(\d+)|\/kind\/([a-z_]+)|\/vessel\/(\d+))?$/.exec(path);
  if (match === null) return null;
  const [, region, eventIndex, kind, vessel] = match;
  return {
    region,
    eventIndex: eventIndex === undefined ? null : Number(eventIndex),
    kind: kind ?? null,
    vessel: vessel === undefined ? null : Number(vessel),
  };
}

/** Region ids are uppercase API tokens; the label is what people read. */
function regionLabel(region: string): string {
  return REGIONS.find((entry) => entry.id === region)?.label ?? region;
}

/** An index is usable only when it is a whole number inside the array. */
function validIndex(index: number | null, length: number): number | null {
  if (index === null || !Number.isInteger(index) || index < 0 || index >= length) return null;
  return index;
}

/** A tiny typed wrapper around createElementNS; attributes never become markup. */
function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    el.setAttribute(name, String(value));
  }
  return el;
}

/**
 * One kind glyph as a 16 px inline SVG. The shapes match the map's marker
 * icons (ring with dashed tail, triangle, twin squares, double ring, crosshair)
 * so a chip teaches the map without a separate legend box.
 */
function kindGlyph(kind: EventKind): SVGSVGElement {
  const glyph = svgEl("svg", {
    class: `kind-glyph is-${kind}`,
    viewBox: "0 0 20 20",
    width: 16,
    height: 16,
    "aria-hidden": "true",
    focusable: "false",
  });

  const stroke = {
    fill: "none",
    stroke: "currentColor",
    "stroke-width": 2,
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
  };

  if (kind === "silence") {
    glyph.append(
      svgEl("circle", { cx: 7, cy: 10, r: 5, ...stroke }),
      svgEl("path", { d: "M13 10h6", ...stroke, "stroke-dasharray": "2 2" }),
    );
  } else if (kind === "jump") {
    glyph.append(svgEl("path", { d: "M10 3 L17.5 16.5 H2.5 Z", ...stroke }));
  } else if (kind === "clone") {
    // The second square is filled like the design's symbol, so the overlap
    // reads as one square in front of the other rather than four crossing lines.
    glyph.append(
      svgEl("rect", { x: 2.5, y: 2.5, width: 9, height: 9, ...stroke }),
      svgEl("rect", {
        x: 8.5,
        y: 8.5,
        width: 9,
        height: 9,
        class: "clone-front",
        ...stroke,
      }),
    );
  } else if (kind === "meeting") {
    glyph.append(
      svgEl("circle", { cx: 10, cy: 10, r: 4, ...stroke }),
      svgEl("circle", { cx: 10, cy: 10, r: 8, ...stroke }),
    );
  } else {
    // spoof: crosshair, the false position mark the map draws.
    glyph.append(
      svgEl("circle", { cx: 10, cy: 10, r: 4, ...stroke }),
      svgEl("path", { d: "M10 2.5v3M10 14.5v3M2.5 10h3M14.5 10h3", ...stroke }),
    );
  }

  return glyph;
}

/**
 * Paint the Live screen into `app`.
 *
 * Returns synchronously with a handle: the fetch continues in the background
 * and destroy() aborts it, so a quick tab switch cannot paint over the screen
 * that replaced this one.
 */
export function renderLive(app: HTMLElement, opts: LiveRoute): LiveScreen {
  const region = opts.region;
  const abort = new AbortController();
  let destroyed = false;

  // The shell exists before the data does, so the bar and the loading line are
  // on screen for the whole fetch instead of flashing empty.
  const main = h(
    "main",
    { class: "main live-main" },
    h("div", { class: "empty" }, "Loading the live recording..."),
  );

  app.replaceChildren(
    topBar({
      active: "live",
      mode: "SNAPSHOT",
      generatedAt: null,
      caseHref: null,
      demoHref: null,
      liveRegion: region,
    }),
    main,
  );

  // The router may hand us an event before the fetch resolves (deep link, or a
  // fast second hashchange). Keep it and apply it in the first paint.
  let pendingIndex = opts.eventIndex;

  // Replaced once the real screen exists; before that a select() only records
  // the intent.
  let selectEvent: (index: number | null) => void = (index) => {
    pendingIndex = index;
  };
  // Same contract for the two route targets that resolve after loading.
  let selectKindAction: (kind: string) => void = () => {};
  let selectVesselAction: (mmsi: number) => void = () => {};
  // Tears down the map once it exists; empty while the fetch is in flight.
  let teardown: () => void = () => {};

  function build(
    summary: LiveSummary,
    events: LiveEvent[],
    vessels: LiveVessel[],
    regions: LiveRegion[],
  ): void {
    // Severity order is the rank order and the URL index; both must be the same
    // array or a deep link would open a different event after a reload.
    const all = sortEvents(events);
    const allIndex = new Map<LiveEvent, number>();
    all.forEach((event, index) => allIndex.set(event, index));

    // One clock for this render: "12 min ago", the detail and the strip then
    // agree, and selecting an event later does not make the list jump. That
    // clock is the snapshot's generated_at, not the browser's now, so the same
    // recording reads identically on any day it is opened.
    const nowMs = snapshotNow(summary.generated_at) ?? Date.now();
    const label = regionLabel(region);
    const hours = windowHours(summary.window);
    const recorded = new Set(regions.map((entry) => entry.region));

    // ---- replay clock ----
    //
    // V1 time travel moves one cursor, `t`, across the recorded window. Every
    // view derives from it: the map draws events with start <= t, the hero
    // counts them, the chips count them per kind, the rail ranks them. The
    // window comes from the summary, so a different snapshot needs no code change.
    const windowStartMs = Date.parse(summary.window.start);
    const windowEndMs = Date.parse(summary.window.end);
    const windowSpanMs = windowEndMs - windowStartMs;
    const hasWindow = Number.isFinite(windowSpanMs) && windowSpanMs > 0;

    // A broken start time is not a reason to hide a finding: it counts as begun.
    const startedAt = new Map<LiveEvent, number>();
    for (const event of all) {
      const at = Date.parse(event.start);
      startedAt.set(event, Number.isFinite(at) ? at : Number.NEGATIVE_INFINITY);
    }

    let replay = false;
    let playing = false;
    let speed: ReplaySpeed = 60;
    let tMs = hasWindow ? windowStartMs : 0;
    /** True while a drag owns the thumb; the animation frame then leaves `t` alone. */
    let scrubbing = false;
    let frame: number | null = null;
    /** The previous animation-frame time; null forces the next frame to restart the delta. */
    let lastFrameMs: number | null = null;

    /** Has the clock reached this event's start? In live view everything has begun. */
    function begun(event: LiveEvent): boolean {
      if (!replay) return true;
      return (startedAt.get(event) ?? Number.NEGATIVE_INFINITY) <= tMs;
    }

    /** Everything the kind filter may show at the current `t`. */
    function upToNow(): LiveEvent[] {
      return replay ? all.filter(begun) : all;
    }

    // A kind route opens its highest-severity event: `all` is already sorted, so
    // the first match is the top one and nothing depends on the API's index.
    const hashed = parseLiveRoute(location.hash);
    const sameHash = hashed !== null && hashed.region === region;
    const requestedKind =
      typeof opts.kind === "string"
        ? opts.kind
        : sameHash && typeof hashed.kind === "string"
          ? hashed.kind
          : null;
    const requestedVessel: number | null =
      opts.vessel ?? (sameHash && hashed.vessel !== null ? hashed.vessel : null) ?? null;
    const kindTop =
      requestedKind !== null && isEventKind(requestedKind)
        ? (all.find((event) => event.kind === requestedKind) ?? null)
        : null;
    const vesselExists =
      requestedVessel !== null && vessels.some((vessel) => vessel.mmsi === requestedVessel);

    let active = new Set<EventKind>(EVENT_KINDS);
    let filtered = eventsOfKinds(all, active);
    let selectedVessel: number | null = vesselExists ? requestedVessel : null;
    let selectedIndex =
      selectedVessel !== null
        ? null
        : kindTop !== null
          ? (allIndex.get(kindTop) ?? null)
          : validIndex(pendingIndex, all.length);

    const selectedEvent = (): LiveEvent | null =>
      selectedIndex === null ? null : (all[selectedIndex] ?? null);

    const selectedVesselRow = (): LiveVessel | null =>
      selectedVessel === null
        ? null
        : (vessels.find((vessel) => vessel.mmsi === selectedVessel) ?? null);

    // ---- stage: map ----

    const mapDiv = h("div", { class: "live-map" });

    // ---- stage: region switcher ----

    const switcher = h("div", {
      class: "live-switch",
      role: "group",
      "aria-label": "Live region",
    });
    for (const entry of REGIONS) {
      const isCurrent = entry.id === region;
      const hasRecording = recorded.has(entry.id);
      const button = h(
        "button",
        {
          class: "live-switch-btn",
          type: "button",
          "aria-pressed": isCurrent ? "true" : "false",
          disabled: !hasRecording,
          title: hasRecording ? null : "No recording yet",
        },
        entry.label,
      );
      // The router paints the new region; this screen must not try to swap its
      // own map and fetch underneath it.
      button.addEventListener("click", () => {
        location.hash = `#/live/${entry.id}`;
      });
      switcher.append(button);
    }

    // ---- stage: hero number + coverage ----

    // The number and its caption are separate nodes because replay rewrites both
    // every frame: the count grows as the clock passes each event's start.
    const heroNum = h(
      "span",
      { class: "live-hero-num" },
      String(unusualCount(summary.counts)),
    );
    const heroLabel = h(
      "span",
      { class: "live-hero-label" },
      `unusual events in the last ${hours} h`,
    );

    const hero = h(
      "div",
      { class: "live-hero" },
      h("div", { class: "live-hero-row" }, heroNum, heroLabel),
      h(
        "div",
        { class: "live-hero-cov" },
        h("span", { class: "live-dot", "aria-hidden": "true" }),
        h(
          "span",
          null,
          `Receivers hearing ${fmtCoverage(summary.coverage.median_msgs_per_min)} msgs/min`,
        ),
      ),
    );

    // ---- stage: attribution ----

    const attribLine = h(
      "div",
      { class: "live-attrib" },
      `Positions: our AIS recording since ${fmtUtc(summary.window.start)} \u00b7 Watchlist: `,
      h(
        "a",
        {
          href: "https://www.opensanctions.org",
          target: "_blank",
          rel: "noopener noreferrer",
        },
        "OpenSanctions",
      ),
    );

    // ---- stage: timeline strip ----

    const track = h("div", { class: "live-axis-track" });
    // The played share of the window sits under the ticks; CSS keeps it hidden
    // until replay, so the live strip looks exactly as before.
    const progressEl = h("span", { class: "live-progress", "aria-hidden": "true" });
    track.append(progressEl);

    // One primary action: start the recording back at its beginning. The same
    // button leaves replay, so there is never a second thing to look for.
    const replayBtn = h(
      "button",
      {
        class: "btn live-replay",
        type: "button",
        "aria-pressed": "false",
        disabled: !hasWindow,
        title: hasWindow
          ? "Play the recording back from its start (Esc leaves replay)"
          : "No recorded window in this snapshot",
      },
      `Replay last ${hours} h`,
    );

    // The right-hand axis label is the clock: "now" live, the cursor time in replay.
    const timeLabel = h("span", null, "now");
    const labels = h(
      "div",
      { class: "live-axis-labels" },
      h("span", null, `-${hours} h`),
      timeLabel,
    );

    const replayControls = replayBar({
      onTogglePlay: () => togglePlay(),
      onCycleSpeed: () => cycleSpeed(),
      onScrubStart: () => {
        scrubbing = true;
      },
      onScrub: (fraction) => {
        seekTo(windowStartMs + fraction * windowSpanMs);
      },
      onScrubEnd: () => {
        scrubbing = false;
        lastFrameMs = null;
      },
    });
    replayControls.element.hidden = true;

    const strip = h(
      "div",
      { class: "live-strip" },
      replayBtn,
      replayControls.element,
      h("div", { class: "live-axis" }, track, labels),
    );

    const stage = h(
      "section",
      { class: "stage live-stage" },
      mapDiv,
      h("div", { class: "live-topleft" }, switcher, hero),
      attribLine,
      strip,
    );

    // ---- rail ----

    const chips = h("div", {
      class: "live-chips",
      role: "group",
      "aria-label": "Filter by kind",
    });
    const rowsEl = h("ol", { class: "live-rows" });
    const listWrap = h(
      "div",
      { class: "live-list" },
      h(
        "div",
        { class: "live-list-head" },
        h("h2", null, "Worth a look"),
        h("span", { class: "meta" }, "highest first"),
      ),
      chips,
      rowsEl,
    );
    // A region with nothing to rank is a result, not a blank: one calm block
    // takes the place of the zero chips and the empty list, in live view and in
    // replay before the first finding begins.
    const emptyEl = h(
      "div",
      { class: "live-empty", hidden: true },
      h("h2", null, "Nothing unusual yet"),
      h(
        "p",
        null,
        `Our receivers heard ${vessels.length} ships here in the last ${hours} h. No silences, impossible jumps, identity clones, spoofed positions or meetings passed our checks.`,
      ),
    );
    // The detail panel is a sibling of the list and starts hidden; the list is
    // hidden instead of rebuilt when an event opens, so going back is instant.
    const detailEl = h("div", { class: "live-detail", hidden: true });
    const rail = h("aside", { class: "rail live-rail" }, listWrap, emptyEl, detailEl);

    // ---- rendering ----

    function markCurrentRow(): void {
      for (const row of rowsEl.querySelectorAll<HTMLButtonElement>(".live-row")) {
        const index = Number(row.dataset.eventIndex);
        if (selectedIndex !== null && index === selectedIndex) {
          row.setAttribute("aria-current", "true");
        } else {
          row.removeAttribute("aria-current");
        }
      }
    }

    /**
     * Each chip's live count node and button, so the replay clock can refresh the
     * numbers every frame without rebuilding the row of controls (and its
     * listeners). Keyed by kind, which is unique on the chip row.
     */
    const chipParts = new Map<EventKind, { button: HTMLButtonElement; count: HTMLSpanElement }>();
    /** Last painted counts, so a frame that reached no new event touches no DOM. */
    let chipSignature = "";
    /** Whether the rail currently shows the calm block instead of the list. */
    let emptyShown = false;

    /**
     * True when the region has nothing a person could look at: no events at all
     * live, and in replay nothing begun by the cursor. This is the one condition
     * for the calm block, so zero chips can never reach the screen.
     */
    function noFindings(): boolean {
      if (!replay) return all.length === 0;
      return reachedCount() === 0;
    }

    /**
     * Paint one chip's count. A zero at the cursor is still a filter worth
     * offering, so the chip stays visible and clickable; it just takes the same
     * 0.45 the stylesheet gives an off chip (this brief owns no stylesheet, and
     * `.live-chip[aria-pressed="false"]` is the existing quiet state).
     */
    function paintChip(kind: EventKind, count: number): void {
      const parts = chipParts.get(kind);
      if (parts === undefined) return;
      parts.count.textContent = String(count);
      parts.button.style.opacity = count === 0 ? "0.45" : "";
    }

    /**
     * Re-count the chips from the set the list ranks: everything the clock has
     * reached, before the pressed-kind filter. `filtered` is that same set
     * narrowed by the chips, so with every chip on the counts add up to the list
     * and a chip can never advertise an event the list cannot show.
     */
    function refreshChipCounts(): void {
      const counts = kindCounts(upToNow());
      const signature = EVENT_KINDS.map((kind) => counts[kind]).join(",");
      if (signature === chipSignature) return;
      chipSignature = signature;
      for (const kind of EVENT_KINDS) paintChip(kind, counts[kind]);
    }

    function renderChips(): void {
      chips.replaceChildren();
      chipParts.clear();
      const counts = kindCounts(upToNow());
      for (const kind of EVENT_KINDS) {
        const on = active.has(kind);
        const count = h("span", { class: "live-chip-count" }, String(counts[kind]));
        const chip = h(
          "button",
          {
            class: "live-chip",
            type: "button",
            "aria-pressed": on ? "true" : "false",
          },
          kindGlyph(kind),
          KIND_LABEL[kind],
          count,
        );
        chip.addEventListener("click", () => toggleKind(kind));
        chipParts.set(kind, { button: chip, count });
        chips.append(chip);
      }
      chipSignature = EVENT_KINDS.map((kind) => counts[kind]).join(",");
      for (const kind of EVENT_KINDS) paintChip(kind, counts[kind]);
    }

    function renderRows(): void {
      rowsEl.replaceChildren();
      // In replay "3 h ago" is measured from the cursor, not the wall clock: the
      // row and the strip must describe the same moment.
      const clock = replay ? tMs : nowMs;
      rankRows(filtered, LIST_ROWS).forEach((event, position) => {
        const index = allIndex.get(event) ?? -1;
        if (index < 0) return;

        const watch = watchlistHit(event, vessels, summary.watchlist);
        const row = h(
          "button",
          {
            class: "live-row",
            type: "button",
            "data-event-index": String(index),
            "aria-current": index === selectedIndex ? "true" : null,
          },
          h(
            "span",
            { class: "live-row-rank" },
            kindGlyph(event.kind),
            h("span", { class: "live-row-index" }, String(position + 1)),
          ),
          h(
            "span",
            { class: "live-row-main" },
            h(
              "span",
              { class: "live-row-head" },
              h("span", { class: "live-row-vessel" }, vesselName(event.mmsi, vessels)),
              h("span", { class: "live-row-mmsi" }, `MMSI ${event.mmsi}`),
            ),
            h("span", { class: "live-row-why" }, event.explanation),
            watch !== null
              ? h("span", { class: "live-row-tag" }, "On sanctions watchlist")
              : null,
          ),
          h("span", { class: "live-row-time" }, timeAgo(event.start, clock)),
        );
        row.addEventListener("click", () => goToEvent(index));
        rowsEl.append(h("li", null, row));
      });
    }

    /**
     * Paint the rail's list: the chips and ranked rows, or, when the region has
     * nothing to rank, one calm block in their place. The empty block is never
     * combined with a row of zero chips, which would look like a filter that
     * could bring something back.
     */
    function renderList(): void {
      emptyShown = noFindings();
      emptyEl.hidden = !emptyShown;
      listWrap.hidden = emptyShown;
      if (emptyShown) {
        chips.replaceChildren();
        chipParts.clear();
        chipSignature = "";
        rowsEl.replaceChildren();
        return;
      }
      renderChips();
      renderRows();
    }

    /** Tick element -> event, so the replay clock can dim future ticks without rebuilding. */
    const tickEvents = new Map<HTMLButtonElement, LiveEvent>();

    function renderTicks(): void {
      // The progress fill and the scrubber stay; only the marks are rebuilt.
      for (const old of track.querySelectorAll(".live-tick, .live-hole")) old.remove();
      tickEvents.clear();

      // Holes first so ticks paint above them: a hole describes the recorder,
      // not a finding, and must never look like one.
      for (const hole of summary.coverage.holes) {
        const from = timelineFraction(hole.start, summary.window);
        const to = timelineFraction(hole.end, summary.window);
        if (to <= from) continue;
        const band = h("span", {
          class: "live-hole",
          title: "Recorder had a gap here",
        });
        band.style.setProperty("--from", String(from));
        band.style.setProperty("--w", String(to - from));
        track.append(band);
      }

      // The strip is the whole window, not the visible slice: during replay the
      // ticks still show what is coming, so `t` has a shape to move through.
      for (const event of eventsOfKinds(all, active)) {
        const index = allIndex.get(event) ?? -1;
        if (index < 0) continue;

        const tick = h("button", {
          class: `live-tick is-${event.kind}`,
          type: "button",
          "aria-label": `${KIND_LABEL[event.kind]} ${vesselName(event.mmsi, vessels)} at ${fmtUtc(event.start)}`,
          "aria-current": index === selectedIndex ? "true" : null,
        });
        tick.style.setProperty("--at", String(timelineFraction(event.start, summary.window)));
        tick.addEventListener("click", () => goToEvent(index));
        tickEvents.set(tick, event);
        track.append(tick);
      }

      markTicks();
    }

    /** Dim the ticks the replay clock has not reached yet; live view has no future. */
    function markTicks(): void {
      for (const [tick, event] of tickEvents) {
        tick.classList.toggle("is-future", !begun(event));
      }
    }

    function renderRail(): void {
      const vessel = selectedVesselRow();
      if (vessel !== null) {
        listWrap.hidden = true;
        emptyEl.hidden = true;
        detailEl.hidden = false;
        detailEl.replaceChildren(
          vesselCard({
            vessel,
            events: all.filter((event) => event.mmsi === vessel.mmsi),
            watch: watchlistForVessel(vessel, summary.watchlist),
            backHref: `#/live/${region}`,
            regionLabel: label,
            generatedAt: summary.generated_at,
            eventHref: (event) => {
              const index = allIndex.get(event);
              return index === undefined ? null : `#/live/${region}/event/${index}`;
            },
          }),
        );
        return;
      }

      const event = selectedEvent();
      if (event === null) {
        detailEl.hidden = true;
        detailEl.replaceChildren();
        // This decides between the list and the calm block; a region with no
        // findings never gets chips or an empty ordered list.
        renderList();
        return;
      }

      listWrap.hidden = true;
      emptyEl.hidden = true;
      detailEl.hidden = false;
      detailEl.replaceChildren(
        eventDetail({
          event,
          vessels,
          watch: watchlistHit(event, vessels, summary.watchlist),
          window: summary.window,
          backHref: `#/live/${region}`,
          regionLabel: label,
          nowMs,
        }),
      );
    }

    /** Push the filtered markers and the current selection to the map. */
    function pushMap(capped: LiveEvent[]): void {
      // The first LIST_ROWS markers carry the same 1..7 rank as the rail's rows.
      // A selection whose kind the chips have hidden, or whose event the replay
      // clock has not reached, is not drawn either: the map must show exactly
      // what is on screen, never a leftover ring.
      const chosen = selectedVessel === null ? selectedEvent() : null;
      const visibleSelection =
        chosen !== null && active.has(chosen.kind) && begun(chosen) ? chosen : null;
      // In replay a marker that just appeared fades in; live view has no arrivals.
      map.setEvents(capped, visibleSelection, LIST_ROWS, replay);
    }

    /**
     * Fly to a vessel's last fix and ring it. The ring is red only when the
     * summary lists the hull as a sanctions or shadow-fleet alert; a detention
     * (or no hit at all) is white, matching the selected-event ring.
     */
    function focusVessel(vessel: LiveVessel): void {
      if (vessel.last === null) {
        map.focusVessel(null);
        map.resetView();
        return;
      }
      const watch = watchlistForVessel(vessel, summary.watchlist);
      const alert =
        watch !== null && (watch.category === "sanctioned" || watch.category === "shadow_fleet");
      map.focusVessel({ vessel, alert });
    }

    function applyFilter(): void {
      filtered = eventsOfKinds(upToNow(), active);
      // The map keeps its own cap; list and strip show everything the filter
      // left, so a chip never hides a finding from the ranked view.
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
      renderList();
      renderTicks();
    }

    function toggleKind(kind: EventKind): void {
      // Clicking the only kind left is an undo, not another isolate: bring
      // every kind back rather than trapping the view on one.
      if (active.size === 1 && active.has(kind)) active = new Set(EVENT_KINDS);
      else active = new Set([kind]);
      applyFilter();
    }

    // ---- replay: clock, scrubber and keyboard ----

    /** How many events the clock has reached, regardless of the kind filter. */
    function reachedCount(): number {
      let total = 0;
      for (const event of all) if (begun(event)) total += 1;
      return total;
    }

    /**
     * The hero number is the one figure on screen, so in replay it counts the
     * findings up to the cursor and names that moment: a growing number with a
     * clock beside it is the whole idea of time travel in one glance.
     */
    function updateHero(): void {
      if (!replay) {
        heroNum.textContent = String(unusualCount(summary.counts));
        heroLabel.textContent = `unusual events in the last ${hours} h`;
        return;
      }
      heroNum.textContent = String(reachedCount());
      heroLabel.textContent = hasWindow
        ? `unusual events by ${fmtUtc(new Date(tMs).toISOString())}`
        : "unusual events so far";
    }

    /** Fraction of the window the cursor sits on, clamped to [0, 1]. */
    function timeFraction(): number {
      if (!hasWindow) return 1;
      return Math.min(1, Math.max(0, (tMs - windowStartMs) / windowSpanMs));
    }

    /** Push `t` to the three places that read it: progress, clock label, thumb. */
    function updateScrub(): void {
      const fraction = timeFraction();
      progressEl.style.setProperty("--at", String(fraction));
      timeLabel.textContent = replay ? fmtUtc(new Date(tMs).toISOString()) : "now";
      replayControls.setFraction(fraction);
    }

    /**
     * Re-derive every time-dependent view from `t`. Only the rail re-renders
     * when the visible set changed; the chips only re-number and the ticks only
     * dim, which is what keeps a 60 fps scrub calm.
     */
    function applyTime(): void {
      const before = filtered.length;
      filtered = eventsOfKinds(upToNow(), active);
      updateHero();
      // The calm block and the list swap the moment the first finding begins;
      // when a proof owns the rail, its hidden list is left alone until back.
      if (detailEl.hidden && noFindings() !== emptyShown) renderList();
      else refreshChipCounts();
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
      // The visible set is monotone in t, so an unchanged count means the same
      // rows; re-rendering them every frame would only cost layout.
      if (filtered.length !== before) renderRows();
      markTicks();
      updateScrub();
    }

    /** Move the cursor to an absolute time; a scrub or a step never invents one. */
    function seekTo(next: number): void {
      if (!hasWindow) return;
      tMs = Math.min(windowEndMs, Math.max(windowStartMs, next));
      lastFrameMs = null;
      applyTime();
    }

    /** One animation frame of playback; `t` advances by wall time at the chosen speed. */
    function frameStep(now: number): void {
      frame = null;
      if (!replay || !playing) return;
      if (lastFrameMs === null) lastFrameMs = now;
      // A backgrounded tab returns with a huge delta; cap it so the window does
      // not jump to the end while nobody was watching.
      const delta = Math.min(Math.max(0, now - lastFrameMs), 250);
      lastFrameMs = now;

      if (!scrubbing) {
        tMs = Math.min(
          windowEndMs,
          tMs + (delta / replayDurationMs(speed)) * windowSpanMs,
        );
        applyTime();
      }

      if (tMs >= windowEndMs) {
        setPlaying(false);
        return;
      }
      frame = window.requestAnimationFrame(frameStep);
    }

    function setPlaying(next: boolean): void {
      playing = next && hasWindow;
      lastFrameMs = null;
      replayControls.setPlaying(playing);
      if (playing && frame === null) frame = window.requestAnimationFrame(frameStep);
      if (!playing && frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
    }

    function togglePlay(): void {
      if (!replay) {
        enterReplay();
        return;
      }
      if (!hasWindow) return;
      // Play pressed at the end replays the window rather than doing nothing.
      if (!playing && tMs >= windowEndMs) {
        tMs = windowStartMs;
        applyTime();
      }
      setPlaying(!playing);
    }

    function cycleSpeed(): void {
      const index = REPLAY_SPEEDS.indexOf(speed);
      speed = REPLAY_SPEEDS[(index + 1) % REPLAY_SPEEDS.length] ?? 60;
      replayControls.setSpeed(speed);
      // The new speed starts from this frame; the old delta must not be billed twice.
      lastFrameMs = null;
    }

    /**
     * Enter replay at the start of the window and run. The press is the user's
     * request for motion, so it plays even under reduced motion; only the
     * marker fade is dropped there. Every entry is a fresh replay at the
     * standard speed, so the window is always the promised ~20 s to a viewer
     * who pressed the button after an earlier demo left 1× behind.
     */
    function enterReplay(): void {
      if (replay || !hasWindow) return;
      replay = true;
      playing = false;
      tMs = windowStartMs;
      speed = 60;
      replayControls.setSpeed(speed);
      strip.classList.add("is-replay");
      replayBtn.textContent = "Exit replay";
      replayBtn.setAttribute("aria-pressed", "true");
      replayControls.element.hidden = false;
      // Laid over the ticks so a drag anywhere on the strip seeks; the ticks stay
      // visible underneath as the shape of what is coming.
      track.append(replayControls.slider);
      applyTime();
      setPlaying(true);
    }

    /** Leave replay: the same snapshot, drawn live again, with no reload. */
    function exitReplay(): void {
      if (!replay) return;
      setPlaying(false);
      replay = false;
      tMs = windowEndMs;
      strip.classList.remove("is-replay");
      replayBtn.textContent = `Replay last ${hours} h`;
      replayBtn.setAttribute("aria-pressed", "false");
      replayControls.element.hidden = true;
      replayControls.slider.remove();
      filtered = eventsOfKinds(upToNow(), active);
      updateHero();
      // Back live, the rail shows the list again (or the calm block if the
      // whole region has nothing); an open proof is left where it is.
      if (detailEl.hidden) renderList();
      renderTicks();
      updateScrub();
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
    }

    /**
     * The keys replay owns. Space and the arrows are only meaningful once the
     * strip is a scrubber, so live view keeps the browser's own behaviour;
     * Escape always leaves. A text field keeps its keys.
     */
    function onKeydown(event: KeyboardEvent): void {
      if (destroyed) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target instanceof HTMLTextAreaElement ||
          target instanceof HTMLSelectElement ||
          (target instanceof HTMLInputElement && target.type !== "range"));
      if (typing) return;

      if (event.key === "Escape") {
        if (!replay) return;
        event.preventDefault();
        exitReplay();
        replayBtn.focus();
        return;
      }

      if (!replay) return;

      if (event.key === " " || event.key === "Spacebar") {
        // Also stops a focused control from acting a second time on keyup.
        event.preventDefault();
        togglePlay();
        return;
      }

      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        // Stepping by hand stops the clock, so the step is legible.
        setPlaying(false);
        seekTo(tMs + (event.key === "ArrowLeft" ? -REPLAY_STEP_MS : REPLAY_STEP_MS));
      }
    }

    replayBtn.addEventListener("click", () => {
      if (replay) exitReplay();
      else enterReplay();
    });
    document.addEventListener("keydown", onKeydown);

    /**
     * Navigate by hash. The router turns that into select(index); going
     * straight to select() would leave the address bar on the old event.
     */
    function goToEvent(index: number): void {
      location.hash = `#/live/${region}/event/${index}`;
    }

    function selectIndex(next: number | null): void {
      if (destroyed) return;
      const resolved = validIndex(next, all.length);
      // Leaving a vessel card for the same index the list already held still
      // has to repaint the rail, so a mode change is never a no-op.
      if (resolved === selectedIndex && selectedVessel === null) return;
      selectedVessel = null;
      selectedIndex = resolved;
      // The ring and name belong to the vessel route alone; going to an event
      // (or back to the list) must not leave them glowing over the map.
      map.focusVessel(null);

      const event = selectedEvent();
      renderRail();
      markCurrentRow();
      renderTicks();
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
      // A finding the replay clock has not reached is not on the map yet; flying
      // to it would zoom into empty sea until its turn comes.
      if (event !== null && begun(event)) map.focusEvent(event);
      else if (event === null) map.resetView();
    }

    function selectKind(kind: string): void {
      if (destroyed || !isEventKind(kind)) return;
      const top = all.find((event) => event.kind === kind) ?? null;
      selectIndex(top === null ? null : (allIndex.get(top) ?? null));
    }

    function selectVessel(mmsi: number): void {
      if (destroyed) return;
      const vessel = vessels.find((entry) => entry.mmsi === mmsi) ?? null;
      if (vessel === null) {
        selectIndex(null);
        return;
      }
      if (selectedVessel === mmsi) return;
      selectedVessel = mmsi;
      selectedIndex = null;
      renderRail();
      markCurrentRow();
      renderTicks();
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
      focusVessel(vessel);
    }

    // ---- attach, then mount the map ----

    main.replaceChildren(stage, rail);
    // Rebuild the bar with the recording's own timestamp now that it is known.
    app.replaceChildren(
      topBar({
        active: "live",
        mode: "SNAPSHOT",
        generatedAt: summary.generated_at,
        caseHref: null,
        demoHref: null,
        liveRegion: region,
      }),
      main,
    );

    // Mounted only after the containers are attached: MapLibre measures the
    // element when it is constructed.
    const map = mountLiveMap({
      container: mapDiv,
      region,
      onSelect: (picked) => {
        // The map hands back the object it was given, whose position in `all`
        // is the URL handle. The router owns the actual select().
        const index = allIndex.get(picked);
        if (index === undefined) return;
        location.hash = `#/live/${region}/event/${index}`;
      },
    });

    map.setVessels(vessels); // once; the AIS field never changes between selections
    renderTicks();
    renderRail();
    markCurrentRow();
    pushMap(filtered.slice(0, MAP_EVENT_CAP));
    updateScrub();

    const initialVessel = selectedVesselRow();
    const initial = selectedEvent();
    if (initialVessel !== null) {
      focusVessel(initialVessel);
    } else if (initial !== null) {
      // A deep link opens already focused. The map fits the region on its own
      // load event, so this can be superseded on the very first frame; every
      // selection after that (the normal path) is not.
      map.focusEvent(initial);
    }

    selectEvent = selectIndex;
    selectKindAction = selectKind;
    selectVesselAction = selectVessel;
    teardown = () => {
      // The key listener outlives the strip; drop the clock and the keys first.
      document.removeEventListener("keydown", onKeydown);
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      map.destroy();
    };
  }

  void Promise.all([
    fetchLiveSummary(region, abort.signal),
    fetchLiveEvents(region, EVENT_LIMIT, abort.signal),
    fetchLiveVessels(region, abort.signal),
    fetchLiveRegions(abort.signal),
  ])
    .then(([summary, events, vessels, regions]) => {
      if (destroyed || abort.signal.aborted) return;
      build(summary, events, vessels, regions);
    })
    .catch((err: unknown) => {
      // Aborted means the router moved on; there is nothing left to draw.
      if (destroyed || abort.signal.aborted) return;
      // Log the real cause: this branch also catches render errors inside build(), not only fetches.
      console.error("live screen failed", err);
      main.replaceChildren(
        h(
          "div",
          { class: "live-error" },
          h(
            "div",
            { class: "banner is-error" },
            `The live recording for ${region} is not available. Start the API with DARKFLEET_DATA=data/snapshot uv run uvicorn darkfleet.api.app:app --port 8000.`,
          ),
        ),
      );
    });

  return {
    select(index: number | null): void {
      // A same-region kind/vessel navigation still reaches this screen as a
      // select() call, so read the route before trusting the event index.
      const route = parseLiveRoute(location.hash);
      if (route !== null && route.region === region) {
        if (typeof route.vessel === "number") {
          selectVesselAction(route.vessel);
          return;
        }
        if (typeof route.kind === "string") {
          selectKindAction(route.kind);
          return;
        }
      }
      selectEvent(index);
    },
    selectKind(kind: string): void {
      selectKindAction(kind);
    },
    selectVessel(mmsi: number): void {
      selectVesselAction(mmsi);
    },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      abort.abort();
      teardown();
    },
  };
}
