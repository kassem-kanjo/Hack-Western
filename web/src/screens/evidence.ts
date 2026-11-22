/**
 * Evidence screen: one question, "how do we know a ship that switched off its transponder is suspicious?"
 *
 * Left panel, top to bottom: the funnel (how 5,624 silences shrink to the ones we cannot
 * explain), how sure we are (validation numbers, read from /api/stats), then the case list.
 * Right: the world with every case as a dot. Selecting a case opens the case file.
 * Every number comes from the API responses; nothing is typed in here.
 */

import type { CaseSummary, CasesResponse, StatsResponse } from "../api";
import { fetchStats } from "../api";
import { attribution } from "../components/Attribution";
import { h } from "../components/dom";
import { topBar } from "../components/TopBar";
import { fmtHours, fmtInt, vesselLabel } from "../format";
import { mountEvidenceMap, type EvidenceFilter } from "../map/evidenceMap";
import { funnelCounts } from "./overview";
import "./evidence.css";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Country names from the two-letter codes the browser already knows. */
const REGION_NAMES = new Intl.DisplayNames(["en"], { type: "region" });

/**
 * The snapshot's three-letter flag codes are ISO alpha-3, while Intl wants
 * alpha-2. This is only the codes that appear in the data, not a full table.
 */
const ALPHA3_TO_ALPHA2: Record<string, string> = {
  BRA: "BR",
  CYP: "CY",
  EST: "EE",
  FIN: "FI",
  IRN: "IR",
  MHL: "MH",
  OMN: "OM",
  PAN: "PA",
  SAU: "SA",
  TWN: "TW",
  TZA: "TZ",
};

/** "CYP" -> "Cyprus"; null when the value is not a country code we can name. */
function countryName(flag: string): string | null {
  const code = flag.trim().toUpperCase();
  const alpha2 = code.length === 2 ? code : code.length === 3 ? ALPHA3_TO_ALPHA2[code] : undefined;
  if (alpha2 === undefined) return null;
  try {
    return REGION_NAMES.of(alpha2) ?? null;
  } catch {
    return null;
  }
}

/** "2026-08-15" -> "15 Aug" (UTC, no locale surprises). */
function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

const f2 = (n: number): string => n.toFixed(2);

/** Unexplained first, then most radar sightings, then longest gap. */
function listOrder(a: CaseSummary, b: CaseSummary): number {
  const ua = a.status === "candidate" ? 0 : 1;
  const ub = b.status === "candidate" ? 0 : 1;
  return ua - ub || b.n_candidates - a.n_candidates || b.hours - a.hours;
}

interface Reading {
  label: string;
  value: string;
  note: string;
}

/** Only rows whose source number exists are returned: a missing file hides its row. */
function readings(stats: StatsResponse): Reading[] {
  const out: Reading[] = [];
  const v = stats.validation;
  if (v && v.null_ratio !== null) {
    const ci = v.null_ci ? ` (CI ${f2(v.null_ci[0])}–${f2(v.null_ci[1])})` : "";
    out.push({
      label: "Satellite radar near a ship that is not broadcasting",
      value: `${f2(v.null_ratio)}× chance${ci}`,
      note: "no better than chance, so we never accuse on radar alone",
    });
  }
  if (v && v.bridge_auc !== null) {
    out.push({
      label: "Where-it-could-have-gone model",
      value: `AUC ${f2(v.bridge_auc)}`,
      note: "puts a ship's true position ahead of a decoy",
    });
  }
  const t = stats.own_track;
  if (t?.hidden_route_inside !== undefined && t.hidden_route_total !== undefined) {
    out.push({
      label: "Own-track test",
      value: `${fmtInt(t.hidden_route_inside)} of ${fmtInt(t.hidden_route_total)}`,
      note: "hidden routes stayed inside the area we drew",
    });
  }
  if (t?.silence_found_receivers_up !== undefined && t.silence_total_receivers_up !== undefined) {
    out.push({
      label: "Transponder-off detector",
      value: `${fmtInt(t.silence_found_receivers_up)} of ${fmtInt(t.silence_total_receivers_up)}`,
      note: "found when our receivers were up",
    });
  }
  return out;
}

function caseRow(c: CaseSummary, onHover: (id: string | null) => void): HTMLElement {
  const unexplained = c.status === "candidate";
  const rawFlag = c.vessel.flag?.trim() ?? "";
  // The code stays visible; the country name is one hover away. A missing flag
  // says what is missing instead of leaving a dash.
  const flag = h(
    "span",
    { class: "ev-flag", title: rawFlag === "" ? null : countryName(rawFlag) },
    rawFlag === "" ? "No flag broadcast" : rawFlag,
  );
  const row = h(
    "a",
    { class: `ev-row${unexplained ? "" : " is-cleared"}`, href: `#/case/${encodeURIComponent(c.id)}` },
    h("span", { class: "ev-mark", "aria-hidden": "true" }),
    h("span", { class: "ev-name" }, vesselLabel(c.vessel)),
    flag,
    h("span", { class: "ev-num" }, fmtHours(c.hours)),
    h("span", { class: "ev-num" }, fmtInt(c.n_candidates)),
  );
  row.addEventListener("mouseenter", () => onHover(c.id));
  row.addEventListener("mouseleave", () => onHover(null));
  return row;
}

export function renderEvidence(root: HTMLElement, data: CasesResponse): { destroy(): void } {
  const counts = funnelCounts(data.cases, data.totals?.gaps_scanned ?? 0, data.totals);
  const sorted = [...data.cases].sort(listOrder);

  let filter: EvidenceFilter = "all";
  const abort = new AbortController();

  // ---- (a) funnel: three calm steps, each one filters the list ----
  const dates = h("span", { class: "ev-dates" });
  const step = (cls: string, n: number, line: (string | HTMLElement)[], target: EvidenceFilter): HTMLButtonElement =>
    h(
      "button",
      { class: `ev-step ${cls}`, type: "button", "data-filter": target },
      h("span", { class: "ev-step-n" }, fmtInt(n)),
      h("span", { class: "ev-step-l" }, ...line),
    );
  const step1 = step("", counts.gapsScanned, ["times a ship switched off its transponder at sea", dates], "all");
  const step2 = step("", counts.withRadar, ["seen by satellite radar where it could have gone"], "all");
  const step3 = step("is-hero", counts.unexplained, ["we still can't explain"], "unexplained");
  const step3b = step(
    "is-sub",
    counts.ruledOut,
    ["cleared, each with a reason"],
    "cleared",
  );
  const stepButtons = [step1, step2, step3, step3b];

  const funnelBlock = h(
    "section",
    { class: "ev-block", "aria-label": "How the cases narrow down" },
    h("h2", null, "How a gap in position reports becomes a case"),
    step1,
    step2,
    h("div", { class: "ev-step-pair" }, step3, step3b),
  );

  // ---- (b) how sure are we ----
  const sureBody = h("dl", { class: "ev-fields" }, h("dt", null, "Loading"), h("dd", null, "validation numbers"));
  const sure = h(
    "section",
    { class: "ev-block" },
    h("h2", null, "How sure are we"),
    sureBody,
    h("div", { class: "ev-src" }, "docs/validation.md · validation-tracks.md · recall-misses.md"),
  );

  // ---- (c) case list ----
  const listHead = h("span", { class: "ev-count" });
  const rows = h("div", { class: "ev-rows" });
  const list = h(
    "section",
    { class: "ev-block ev-list" },
    h("h2", null, "Cases ", listHead),
    h(
      "div",
      { class: "ev-cols" },
      h("span", null),
      h("span", null, "Ship"),
      h("span", null, "Flag"),
      h("span", { class: "ev-num" }, "Gap"),
      h("span", { class: "ev-num" }, "Radar"),
    ),
    rows,
  );

  const mapDiv = h("div", { id: "map", class: "map" });
  const stage = h(
    "section",
    { class: "stage ev-stage" },
    mapDiv,
    h(
      "div",
      { class: "ev-key" },
      h("span", { class: "ev-key-dot is-open" }),
      "can't explain",
      h("span", { class: "ev-key-dot is-cleared" }),
      "cleared",
      h("span", { class: "ev-key-hint" }, "dot = where the ship switched off its transponder · click to open"),
    ),
    attribution(),
  );

  const panel = h(
    "aside",
    { class: "ev-panel" },
    h(
      "div",
      { class: "ev-ask" },
      "How do we know a ship that switched off its transponder is really suspicious?",
    ),
    funnelBlock,
    sure,
    list,
  );

  root.replaceChildren(
    topBar({ active: "overview", mode: "SNAPSHOT", generatedAt: data.generated_at }),
    h("main", { class: "main ev-main" }, panel, stage),
  );

  const map = mountEvidenceMap(mapDiv, data.cases);

  const draw = (): void => {
    const shown = sorted.filter(
      (c) => filter === "all" || (filter === "unexplained") === (c.status === "candidate"),
    );
    rows.replaceChildren(...shown.map((c) => caseRow(c, (id) => map.highlight(id))));
    listHead.textContent = `· ${fmtInt(shown.length)} shown`;
    for (const b of stepButtons) {
      // Steps 1 and 2 both mean "everything"; only a real filter is marked.
      const on = filter !== "all" && b.dataset.filter === filter;
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
    map.setFilter(filter);
  };

  for (const b of stepButtons) {
    b.addEventListener("click", () => {
      const next = b.dataset.filter as EvidenceFilter;
      // Clicking the active filter again goes back to the full list.
      filter = filter === next && next !== "all" ? "all" : next;
      draw();
    });
  }
  draw();

  fetchStats(abort.signal)
    .then((stats) => {
      if (abort.signal.aborted) return;
      if (stats.gap_window) {
        dates.textContent = ` (${shortDate(stats.gap_window.start)}–${shortDate(stats.gap_window.end)})`;
      }
      const rowsData = readings(stats);
      if (rowsData.length === 0) {
        sure.remove();
        return;
      }
      sureBody.replaceChildren(
        ...rowsData.flatMap((r) => [
          h("dt", null, r.label),
          h("dd", null, h("span", { class: "ev-val" }, r.value), h("span", { class: "ev-note" }, r.note)),
        ]),
      );
    })
    .catch(() => {
      if (!abort.signal.aborted) sure.remove();
    });

  return {
    destroy(): void {
      abort.abort();
      map.destroy();
    },
  };
}
