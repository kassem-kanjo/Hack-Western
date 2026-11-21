/**
 * Case file screen: one gap in detail. Lens map on top of the stage, timeline
 * below it, the rail carries identity, score, sources and caveats.
 *
 * The screen owns its fetch, map and timers so the router only has to call
 * `destroy()` before painting the next route.
 */

import type { CaseDetail, CaseSummary } from "../api";
import { fetchCase } from "../api";
import { attribution } from "../components/Attribution";
import { caseRail } from "../components/CaseRail";
import { h } from "../components/dom";
import { caseLayerPanel } from "../components/Legend";
import { timeline, type TimelineHandle } from "../components/Timeline";
import { topBar } from "../components/TopBar";
import { fmtHours, fmtInt, vesselLabel } from "../format";
import { mountCaseMap, type CaseMapHandle } from "../map/caseMap";
import { nearestFrameIndex } from "../model/frames";
import { demoCaseId } from "./overview";

const API_DOWN =
  "The console could not reach the API. Start it with DARKFLEET_DATA=data/snapshot uv run uvicorn darkfleet.api.app:app --port 8000.";

/**
 * Only one case fetch can be live at a time. A new case file aborts the one
 * still in flight; its continuation then finds the signal aborted and drops its
 * stale result instead of repainting the screen.
 */
let activeAbort: AbortController | null = null;

/**
 * Paint the case file into `root` (the #app element).
 *
 * `ranked` is the overview's order, passed in so a later prev/next control can
 * reuse it without ranking again.
 */
export async function renderCaseFile(
  root: HTMLElement,
  id: string,
  ranked: CaseSummary[],
  generatedAt: string | null,
): Promise<{ destroy(): void }> {
  activeAbort?.abort();
  const abort = new AbortController();
  activeAbort = abort;

  const caseHref = `#/case/${encodeURIComponent(id)}`;
  const demo = demoCaseId(ranked);

  // The shell is built before the fetch resolves so the topbar and the mode badge
  // are stable while the rail fills in; those nodes also tell us whether this
  // render is still on screen after the await.
  const stage = h(
    "section",
    { class: "stage stage-case" },
    h("div", { class: "empty" }, "Loading the case file..."),
  );
  const rail = h("aside", { class: "rail" });

  root.replaceChildren(
    topBar({
      active: "case",
      mode: "SNAPSHOT",
      generatedAt,
      caseHref,
      demoHref: demo !== null ? `#/case/${encodeURIComponent(demo)}` : null,
    }),
    h("main", { class: "main" }, stage, rail),
  );

  let detail: CaseDetail;
  try {
    detail = await fetchCase(id, abort.signal);
  } catch {
    // Aborted means the user navigated away; there is nothing left to draw.
    if (abort.signal.aborted || !stage.isConnected) return { destroy(): void {} };

    stage.replaceChildren(h("div", { class: "empty" }, "No case data."));
    rail.append(h("div", { class: "banner is-error" }, API_DOWN));
    return {
      destroy(): void {
        abort.abort();
      },
    };
  }

  if (abort.signal.aborted || !stage.isConnected) return { destroy(): void {} };

  // `n_candidates` counts every radar pass, including ones already ruled out.
  // "Candidate sightings" is the label the analyst reads, so count the verdicts.
  const candidateSightings = detail.candidates.filter(
    (candidate) => candidate.verdict === "candidate",
  ).length;

  // Earliest radar pass, not the first row in the API array: the demo link
  // promises "first radar pass" for the ruled-out case it opens.
  const earliestPass = detail.candidates
    .flatMap((candidate) =>
      candidate.scene_time === null
        ? []
        : [{ candidate, at: Date.parse(candidate.scene_time) }],
    )
    .sort((a, b) => a.at - b.at)[0]?.candidate;

  let caseMap: CaseMapHandle | null = null;
  const mapDiv = h("div", { id: "map", class: "map" });
  const mapArea = h(
    "div",
    { class: "map-area" },
    mapDiv,
    caseLayerPanel((group, visible) => caseMap?.setLayerVisible(group, visible)),
    attribution(),
  );

  stage.replaceChildren(
    h(
      "div",
      { class: "map-chips" },
      h("a", { class: "chip-pill", href: "#/evidence" }, "Cases"),
      h("span", { class: "chip-pill" }, vesselLabel(detail.vessel)),
      h("span", { class: "chip-pill is-gap" }, `Gap ${fmtHours(detail.hours)}`),
      h(
        "span",
        { class: "chip-pill" },
        `${fmtInt(candidateSightings)} of ${fmtInt(detail.candidates.length)} radar detections could be this ship`,
      ),
    ),
    mapArea,
  );
  try {
    rail.append(caseRail(detail));
  } catch (error) {
    // A malformed field must not take the map down with it (the sanctions-entry bug did).
    console.error("case rail failed", error);
    rail.append(h("div", { class: "banner is-error" }, "Part of this case file could not be drawn."));
  }

  // The timeline is built before the map mounts, but it only notifies on user
  // input, so the explicit setFrame(0) below is the first frame the map sees.
  const tl: TimelineHandle = timeline(detail, {
    onFrame(index) {
      caseMap?.setFrame(index);
    },
  });

  // Timeline goes into the auto row under the map, then the map is mounted: the
  // container is at its final height before MapLibre measures it.
  stage.append(tl.element);
  caseMap = mountCaseMap(mapDiv, detail);
  // Open at the first radar pass so the lens and a detection are on screen at
  // once, rather than an empty point at t = 0.
  const startFrame = earliestPass?.scene_time
    ? nearestFrameIndex(detail.lens_frames, earliestPass.scene_time)
    : 0;
  caseMap.setFrame(startFrame);
  tl.setFrame(startFrame);

  return {
    destroy(): void {
      tl.destroy();
      caseMap?.destroy();
      abort.abort();
      if (activeAbort === abort) activeAbort = null;
    },
  };
}
