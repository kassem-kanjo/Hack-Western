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
 */

import "../styles/live.css";

import { fetchLiveEvents, fetchLiveRegions, fetchLiveSummary, fetchLiveVessels } from "../api";
import { eventDetail } from "../components/EventDetail";
import { h } from "../components/dom";
import { topBar } from "../components/TopBar";
import { fmtUtc } from "../format";
import { mountLiveMap } from "../map/liveMap";
import type { EventKind, LiveEvent, LiveRegion, LiveSummary, LiveVessel } from "../model/live";
import { EVENT_KINDS } from "../model/live";
import {
  KIND_LABEL,
  eventsOfKinds,
  fmtCoverage,
  rankRows,
  sortEvents,
  timeAgo,
  timelineFraction,
  unusualCount,
  vesselName,
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

const SVG_NS = "http://www.w3.org/2000/svg";

export interface LiveScreen {
  destroy(): void;
  /** Called by the router when only the event part of the hash changed. Must NOT re-fetch or rebuild the map. */
  select(index: number | null): void;
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

/** Chip counts come from every event, not from the filtered view, so a chip never lies. */
function kindCounts(events: LiveEvent[]): Record<EventKind, number> {
  const counts: Record<EventKind, number> = {
    silence: 0,
    jump: 0,
    clone: 0,
    meeting: 0,
    spoof: 0,
  };
  for (const event of events) counts[event.kind] += 1;
  return counts;
}

/**
 * Paint the Live screen into `app`.
 *
 * Returns synchronously with a handle: the fetch continues in the background
 * and destroy() aborts it, so a quick tab switch cannot paint over the screen
 * that replaced this one.
 */
export function renderLive(
  app: HTMLElement,
  opts: { region: string; eventIndex: number | null },
): LiveScreen {
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
    // agree, and selecting an event later does not make the list jump.
    const nowMs = Date.now();
    const label = regionLabel(region);
    const hours = windowHours(summary.window);
    const recorded = new Set(regions.map((entry) => entry.region));
    const counts = kindCounts(all);

    let active = new Set<EventKind>(EVENT_KINDS);
    let filtered = eventsOfKinds(all, active);
    let selectedIndex = validIndex(pendingIndex, all.length);

    const selectedEvent = (): LiveEvent | null =>
      selectedIndex === null ? null : (all[selectedIndex] ?? null);

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

    const hero = h(
      "div",
      { class: "live-hero" },
      h(
        "div",
        { class: "live-hero-row" },
        h("span", { class: "live-hero-num" }, String(unusualCount(summary.counts))),
        h("span", { class: "live-hero-label" }, `unusual events in the last ${hours} h`),
      ),
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
    const replay = h(
      "button",
      { class: "btn live-replay", type: "button", disabled: true, title: "Coming in V2" },
      "Replay last 24 h",
    );
    const strip = h(
      "div",
      { class: "live-strip" },
      replay,
      h(
        "div",
        { class: "live-axis" },
        track,
        h(
          "div",
          { class: "live-axis-labels" },
          h("span", null, `-${hours} h`),
          h("span", null, "now"),
        ),
      ),
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
    // The detail panel is a sibling of the list and starts hidden; the list is
    // hidden instead of rebuilt when an event opens, so going back is instant.
    const detailEl = h("div", { class: "live-detail", hidden: true });
    const rail = h("aside", { class: "rail live-rail" }, listWrap, detailEl);

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

    function renderChips(): void {
      chips.replaceChildren();
      for (const kind of EVENT_KINDS) {
        const on = active.has(kind);
        const chip = h(
          "button",
          {
            class: "live-chip",
            type: "button",
            "aria-pressed": on ? "true" : "false",
          },
          kindGlyph(kind),
          KIND_LABEL[kind],
          h("span", { class: "live-chip-count" }, String(counts[kind])),
        );
        chip.addEventListener("click", () => toggleKind(kind));
        chips.append(chip);
      }
    }

    function renderRows(): void {
      rowsEl.replaceChildren();
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
          h("span", { class: "live-row-time" }, timeAgo(event.start, nowMs)),
        );
        row.addEventListener("click", () => goToEvent(index));
        rowsEl.append(h("li", null, row));
      });
    }

    function renderTicks(): void {
      track.replaceChildren();

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

      for (const event of filtered) {
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
        track.append(tick);
      }
    }

    function renderRail(): void {
      const event = selectedEvent();
      if (event === null) {
        listWrap.hidden = false;
        detailEl.hidden = true;
        detailEl.replaceChildren();
        return;
      }

      listWrap.hidden = true;
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
      // A selection whose kind the chips have hidden is not drawn either: the
      // map must show exactly the kind on screen, never a leftover ring.
      const chosen = selectedEvent();
      const visibleSelection = chosen !== null && active.has(chosen.kind) ? chosen : null;
      map.setEvents(capped, visibleSelection, LIST_ROWS);
    }

    function applyFilter(): void {
      filtered = eventsOfKinds(all, active);
      // The map keeps its own cap; list and strip show everything the filter
      // left, so a chip never hides a finding from the ranked view.
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
      renderChips();
      renderRows();
      renderTicks();
    }

    function toggleKind(kind: EventKind): void {
      // Clicking the only kind left is an undo, not another isolate: bring
      // every kind back rather than trapping the view on one.
      if (active.size === 1 && active.has(kind)) active = new Set(EVENT_KINDS);
      else active = new Set([kind]);
      applyFilter();
    }

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
      if (resolved === selectedIndex) return;
      selectedIndex = resolved;

      const event = selectedEvent();
      renderRail();
      markCurrentRow();
      renderTicks();
      pushMap(filtered.slice(0, MAP_EVENT_CAP));
      if (event !== null) map.focusEvent(event);
      else map.resetView();
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
    renderChips();
    renderRows();
    renderTicks();
    renderRail();
    markCurrentRow();
    pushMap(filtered.slice(0, MAP_EVENT_CAP));

    const initial = selectedEvent();
    if (initial !== null) {
      // A deep link opens already focused. The map fits the region on its own
      // load event, so this can be superseded on the very first frame; every
      // selection after that (the normal path) is not.
      map.focusEvent(initial);
    }

    selectEvent = selectIndex;
    teardown = () => {
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
      selectEvent(index);
    },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      abort.abort();
      teardown();
    },
  };
}
