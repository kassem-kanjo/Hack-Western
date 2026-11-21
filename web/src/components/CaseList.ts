import type { CaseStatus, CaseSummary } from "../api";
import { fmtHours, fmtInt, vesselLabel } from "../format";
import { h } from "./dom";
import { riskChip } from "./RiskChip";

const BAND_BAR_CLASS = {
  high: "is-high",
  medium: "is-medium",
  low: "is-low",
} as const;

function statusChip(status: CaseStatus): HTMLElement {
  if (status === "candidate") return h("span", { class: "chip is-unexplained" }, "Unexplained");
  if (status === "ruled_out") return h("span", { class: "chip is-ruled-out" }, "Ruled out");
  return h("span", { class: "chip is-none" }, "No evidence");
}

function caseRow(c: CaseSummary, index: number, selectedId: string | null): HTMLElement {
  const flag = c.vessel.flag?.trim() || "no flag";
  const type = c.vessel.type ? c.vessel.type.replace(/_/g, " ") : "unknown type";

  const middle = h(
    "div",
    null,
    h("div", { class: "vessel" }, vesselLabel(c.vessel)),
    h("div", { class: "sub" }, `${flag} \u00b7 ${type}`),
    h(
      "div",
      { class: "sub" },
      statusChip(c.status),
      h("span", { class: "mono" }, ` ${fmtInt(c.n_candidates)} radar`),
    ),
  );

  if (c.score !== null) {
    const pct = Math.min(100, Math.max(0, c.score));
    const fill = h("i", null);
    fill.style.width = `${pct}%`;
    const bandClass = c.band !== null ? BAND_BAR_CLASS[c.band] : "is-none";
    middle.append(h("div", { class: `bar ${bandClass}` }, fill));
  }

  const right = h(
    "div",
    { class: "right" },
    h("span", { class: "hours mono" }, fmtHours(c.hours)),
    c.score !== null ? h("span", { class: "score" }, riskChip(c.band, c.score)) : null,
  );

  const selected = c.id === selectedId;
  return h(
    "a",
    {
      class: `case-row${selected ? " is-selected" : ""}`,
      href: `#/case/${encodeURIComponent(c.id)}`,
    },
    h("span", { class: "rank" }, fmtInt(index + 1)),
    middle,
    right,
  );
}

/** Ranked case rows as an ordered list of links. Ordering is the caller's job. */
export function caseList(cases: CaseSummary[], selectedId: string | null): HTMLElement {
  const section = h(
    "section",
    { class: "case-list" },
    h(
      "div",
      { class: "rail-head" },
      h("span", null, "Cases, ranked"),
      h("span", { class: "meta" }, `${fmtInt(cases.length)} shown`),
    ),
  );

  if (cases.length === 0) {
    section.append(h("div", { class: "empty" }, "No cases in this snapshot."));
    return section;
  }

  for (const [index, c] of cases.entries()) {
    section.append(caseRow(c, index, selectedId));
  }
  return section;
}
