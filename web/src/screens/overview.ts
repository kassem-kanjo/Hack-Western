/**
 * Overview screen: the whole snapshot at once. KPI strip over the map, ranked
 * case list and funnel in the rail. Pure layout; every number comes from the
 * frozen API response, not from local maths.
 */

import type { CaseSummary, CasesResponse } from "../api";
import { attribution } from "../components/Attribution";
import { caseList } from "../components/CaseList";
import { h } from "../components/dom";
import { evidencePanel } from "../components/EvidencePanel";
import { funnel } from "../components/Funnel";
import { kpiStrip } from "../components/KpiStrip";
import { legend } from "../components/Legend";
import { topBar } from "../components/TopBar";
import { fmtInt } from "../format";
import { mountOverviewMap } from "../map/overviewMap";

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
 * KPI and funnel numbers.
 *
 * `withRadar` is deliberately the number of listed cases: `totals.with_candidates`
 * in the snapshot reads 99, which disagrees with the frozen demo script (109),
 * so the listed rows are the source of truth for what the console shows.
 */
export function funnelCounts(
  cases: CaseSummary[],
  gapsScanned: number,
): { gapsScanned: number; withRadar: number; unexplained: number; ruledOut: number } {
  return {
    gapsScanned,
    withRadar: cases.length,
    unexplained: cases.filter((c) => c.status === "candidate").length,
    ruledOut: cases.filter((c) => c.status === "ruled_out").length,
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

/** Paint the overview into `root` (the #app element). */
export function renderOverview(
  root: HTMLElement,
  data: CasesResponse,
): { destroy(): void } {
  const ranked = rankCases(data.cases);
  const counts = funnelCounts(data.cases, data.totals.gaps_scanned);
  const best = bestCandidateId(ranked);
  const demo = demoCaseId(ranked);

  const mapDiv = h("div", { id: "map", class: "map" });

  const stage = h(
    "section",
    { class: "stage stage-overview" },
    kpiStrip(counts),
    h(
      "div",
      { class: "map-chips" },
      h("span", { class: "chip-pill" }, h("span", { class: "k" }, "Region"), " Global snapshot"),
      h("span", { class: "chip-pill" }, `Showing all ${fmtInt(ranked.length)} radar cases`),
    ),
    mapDiv,
    legend("overview"),
    attribution(),
  );

  // The evidence panel reads /api/stats itself and hides when the API has no
  // validation block, so the funnel and case list never wait on it.
  const evidence = evidencePanel();

  const rail = h(
    "aside",
    { class: "rail" },
    funnel(counts),
    evidence.element,
    caseList(ranked, null),
  );

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
  const map = mountOverviewMap(mapDiv, ranked);

  return {
    destroy(): void {
      evidence.destroy();
      map.destroy();
    },
  };
}
