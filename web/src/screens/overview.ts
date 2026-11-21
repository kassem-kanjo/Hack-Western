/**
 * Overview screen, v2: one funnel over the map, one hero number, one ranked list.
 *
 * The page answers one question - how much is suspicious, and how much is
 * cleared? - with a single 5,624 -> 109 -> 19 funnel. 19 is the only coloured
 * headline number and the only thing drawn on the map; the 90 cleared gaps stay
 * hidden behind the list's switch. Every number comes from the frozen API
 * response, not from local maths.
 */

import type { CaseSummary, CasesResponse } from "../api";
import { attribution } from "../components/Attribution";
import { h } from "../components/dom";
import { evidencePanel } from "../components/EvidencePanel";
import { hormuzBlock } from "../components/hormuz";
import { topBar } from "../components/TopBar";
import { fmtHours, fmtInt, vesselLabel } from "../format";
import { mountOverviewMap } from "../map/overviewMap";
import "./overview.css";

/** How many list rows are visible before "Show N more". */
const VISIBLE_ROWS = 6;

/**
 * Analyst order: highest risk score first, cases with no score last, and the
 * longest gap as the tie-breaker.
 *
 * The API already sorts, but the rail must not change order just because a
 * server build does: the client sorts the same way every time.
 *
 * `sort` is stable in ES2019+, so full ties keep the order the API returned.
 * The input array is copied because sorting in place would mutate the cached
 * response shared by both screens.
 */
export function rankCases(cases: CaseSummary[]): CaseSummary[] {
  return [...cases].sort((a, b) => {
    // Null means "not scored", which is not the same as zero: it ranks last.
    if (a.score === null && b.score === null) return b.hours - a.hours;
    if (a.score === null) return 1;
    if (b.score === null) return -1;
    return b.score - a.score || b.hours - a.hours;
  });
}

/**
 * Funnel numbers.
 *
 * `totals` is authoritative when present: `with_candidates` is the 109 radar
 * baseline before the physics checks and `ruled_out` is the 90 cleared by them.
 * `candidates_final` is optional because older schema builds omit it; the
 * listed cases are always the fallback so the funnel stays consistent.
 */
export interface FunnelTotals {
  with_candidates?: number;
  candidates_final?: number;
  ruled_out?: number;
}

export function funnelCounts(
  cases: CaseSummary[],
  gapsScanned: number,
  totals?: FunnelTotals | null,
): { gapsScanned: number; withRadar: number; unexplained: number; ruledOut: number } {
  const unexplained = totals?.candidates_final ?? cases.filter((c) => c.status === "candidate").length;
  const withRadar = totals?.with_candidates ?? cases.length;
  return {
    gapsScanned,
    withRadar,
    unexplained,
    ruledOut: totals?.ruled_out ?? Math.max(0, withRadar - unexplained),
  };
}

/** The case the topbar "Case file" tab opens: the top-ranked unexplained one. */
export function bestCandidateId(ranked: CaseSummary[]): string | null {
  const best = ranked.find((c) => c.status === "candidate");
  return best ? best.id : null;
}

/**
 * The case "Start demo" opens: a physics-ruled-out gap with at least one radar
 * pass, lowest score first. It is the honest version of the pitch (the ship
 * was not there), and the case file opens it on the first radar pass.
 */
export function demoCaseId(ranked: CaseSummary[]): string | null {
  let best: CaseSummary | null = null;
  for (const candidate of ranked) {
    if (candidate.status !== "ruled_out" || candidate.n_candidates < 1) continue;
    if (best === null || (candidate.score ?? Infinity) < (best.score ?? Infinity)) {
      best = candidate;
    }
  }
  return best ? best.id : null;
}

function funnelStep(value: number, label: string, hero = false): HTMLElement {
  return h(
    "div",
    { class: `fs${hero ? " is-hero" : ""}` },
    h("span", { class: "n" }, fmtInt(value)),
    h("span", { class: "l" }, label),
  );
}

function arrow(caption?: string): HTMLElement {
  return h(
    "div",
    { class: "fa" },
    caption !== undefined ? h("span", { class: "fa-cap" }, caption) : null,
    h("span", { class: "fa-arrow", "aria-hidden": "true" }, "\u2192"),
  );
}

/** The single funnel that replaces the old KPI strip and rail bar chart. */
function funnelOverlay(counts: {
  gapsScanned: number;
  withRadar: number;
  unexplained: number;
  ruledOut: number;
}): HTMLElement {
  return h(
    "section",
    { class: "funnel", "aria-label": "From gaps to unexplained" },
    funnelStep(counts.gapsScanned, "times a ship went silent at sea"),
    arrow(),
    funnelStep(counts.withRadar, "had radar nearby"),
    arrow(`${fmtInt(counts.ruledOut)} cleared by physics and satellite timing`),
    funnelStep(counts.unexplained, "we cannot explain", true),
  );
}

/** Map key: one line, bottom right, no box. */
function overviewLegend(): HTMLElement {
  return h(
    "div",
    { class: "overview-legend" },
    h("span", { class: "dot", "aria-hidden": "true" }),
    h("span", null, "Ship we cannot explain"),
    h("span", { class: "cleared" }, "Cleared ships are hidden"),
  );
}

/** Name, flag and type in grey; one sentence of evidence; one amber number. */
function shipRow(c: CaseSummary): HTMLElement {
  const flag = c.vessel.flag?.trim() || "no flag";
  const type = c.vessel.type ? c.vessel.type.replace(/_/g, " ") : "unknown type";
  const sightings = `${fmtInt(c.n_candidates)} radar sighting${c.n_candidates === 1 ? "" : "s"}`;

  return h(
    "a",
    {
      class: "ship-row",
      href: `#/case/${encodeURIComponent(c.id)}`,
    },
    h(
      "div",
      { class: "ship-main" },
      h("div", { class: "vessel" }, vesselLabel(c.vessel)),
      h("div", { class: "sub" }, `${flag} \u00b7 ${type}`),
      h("div", { class: "sub evidence" }, sightings),
    ),
    h(
      "div",
      { class: "right" },
      h("span", { class: "hours mono" }, fmtHours(c.hours)),
      h("span", { class: "unit" }, "dark"),
    ),
  );
}

function segmentButton(label: string, count: number, dot: string): HTMLButtonElement {
  return h(
    "button",
    { class: "seg-btn", type: "button", "aria-pressed": "false" },
    h("span", { class: `dot ${dot}`, "aria-hidden": "true" }),
    label,
    h("span", { class: "n" }, fmtInt(count)),
  );
}

/** Paint the overview into `root` (the #app element). */
export function renderOverview(
  root: HTMLElement,
  data: CasesResponse,
): { destroy(): void } {
  const ranked = rankCases(data.cases);
  const counts = funnelCounts(data.cases, data.totals?.gaps_scanned ?? 0, data.totals);
  const best = bestCandidateId(ranked);
  const demo = demoCaseId(ranked);

  // The map shows only the cases we cannot explain; the 90 cleared gaps stay
  // hidden until the list switch asks for them.
  const unexplained = ranked.filter((c) => c.status === "candidate");
  const cleared = ranked.filter((c) => c.status === "ruled_out");

  const mapDiv = h("div", { id: "map", class: "map" });
  const stage = h(
    "section",
    { class: "stage stage-overview" },
    funnelOverlay(counts),
    mapDiv,
    overviewLegend(),
    attribution(),
  );

  // The evidence panel reads /api/stats itself and hides when the API has no
  // validation block, so the funnel and case list never wait on it.
  const evidence = evidencePanel();

  const rows = h("div", { class: "ship-rows" });
  const moreButton = h("button", { class: "link-btn", type: "button" }, "");
  const evidenceButton = h(
    "button",
    { class: "link-btn", type: "button", "aria-expanded": "false" },
    "How sure are we? ",
    h("span", { "aria-hidden": "true" }, "\u203a"),
  );
  const foot = h(
    "div",
    { class: "case-foot" },
    moreButton,
    evidenceButton,
  );
  const evidenceSlot = h("div", { class: "evidence-slot", hidden: true }, evidence.element);

  const unexplainedButton = segmentButton("Unexplained", counts.unexplained, "is-gap");
  const clearedButton = segmentButton("Cleared", counts.ruledOut, "is-cleared");
  const segment = h(
    "div",
    { class: "seg", role: "group", "aria-label": "Which ships to list" },
    unexplainedButton,
    clearedButton,
  );

  // Sort the list by the evidence the rows print, not by the hidden score: a
  // reader can see why a ship is above another one.
  const order = (a: CaseSummary, b: CaseSummary): number =>
    b.n_candidates - a.n_candidates || b.hours - a.hours;

  const list = h(
    "section",
    { class: "case-list ships" },
    h(
      "div",
      { class: "rail-head" },
      h("span", null, "Ships to review"),
      h("span", { class: "meta" }, "most radar sightings first"),
    ),
    segment,
    rows,
    foot,
  );

  let mode: "unexplained" | "cleared" = "unexplained";
  let expanded = false;

  function draw(): void {
    const pool = mode === "unexplained" ? unexplained : cleared;
    const sorted = [...pool].sort(order);
    const visible = expanded ? sorted : sorted.slice(0, VISIBLE_ROWS);

    rows.replaceChildren(...visible.map(shipRow));
    if (sorted.length === 0) {
      rows.append(h("div", { class: "empty" }, "No cases in this snapshot."));
    }

    unexplainedButton.setAttribute("aria-pressed", mode === "unexplained" ? "true" : "false");
    clearedButton.setAttribute("aria-pressed", mode === "cleared" ? "true" : "false");

    const hiddenCount = Math.max(0, sorted.length - VISIBLE_ROWS);
    moreButton.hidden = hiddenCount <= 0;
    moreButton.textContent = expanded ? "Show fewer" : `Show ${fmtInt(hiddenCount)} more`;
  }

  moreButton.addEventListener("click", () => {
    expanded = !expanded;
    draw();
  });
  unexplainedButton.addEventListener("click", () => {
    mode = "unexplained";
    expanded = false;
    draw();
  });
  clearedButton.addEventListener("click", () => {
    mode = "cleared";
    expanded = false;
    draw();
  });
  evidenceButton.addEventListener("click", () => {
    // The panel removes itself when the snapshot has no validation block.
    if (!evidence.element.isConnected) {
      evidenceButton.hidden = true;
      return;
    }
    const open = evidenceSlot.hidden;
    evidenceSlot.hidden = !open;
    evidenceButton.setAttribute("aria-expanded", open ? "true" : "false");
  });

  draw();

  const hormuzSlot = h("div", { class: "hormuz-slot" });
  const rail = h("aside", { class: "rail" }, list, evidenceSlot, hormuzSlot);

  root.replaceChildren(
    topBar({
      active: "overview",
      mode: "SNAPSHOT",
      generatedAt: data.generated_at,
      caseHref: best !== null ? `#/case/${encodeURIComponent(best)}` : null,
      demoHref: demo !== null ? `#/case/${encodeURIComponent(demo)}` : null,
    }),
    h("main", { class: "main" }, stage, rail),
  );

  // Mount after the container is attached: MapLibre measures the element when it
  // is created and would otherwise size itself to a detached 0 x 0 node.
  const map = mountOverviewMap(mapDiv, unexplained);
  const hormuz = hormuzBlock(map.map);
  hormuzSlot.append(hormuz.element);

  return {
    destroy(): void {
      hormuz.destroy();
      evidence.destroy();
      map.destroy();
    },
  };
}
