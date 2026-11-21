/**
 * The right-hand case file rail: identity, risk score, sanctions, the reason a
 * gap was flagged (or ruled out), the data sources and the caveats.
 *
 * Everything is built with `h()`, so snapshot text is never parsed as markup.
 */

import type { CaseDetail, Candidate, Sanctions } from "../api";
import { fmtInt, vesselLabel } from "../format";
import { h } from "./dom";
import { riskChip } from "./RiskChip";

/** "candidate" and "ruled_out" get their own heading; anything else is neutral. */
function reasonHeading(status: string): string {
  if (status === "candidate") return "Why it was flagged";
  if (status === "ruled_out") return "Why it was ruled out";
  return "What we checked";
}

function caseHead(detail: CaseDetail): HTMLElement {
  const mmsi = detail.vessel.mmsi?.trim() || "unknown";
  const flag = detail.vessel.flag?.trim() || "unknown";
  const type = detail.vessel.type ? detail.vessel.type.replace(/_/g, " ") : "unknown type";

  return h(
    "div",
    { class: "case-head" },
    h("div", { class: "name case-name" }, vesselLabel(detail.vessel)),
    h("div", { class: "sub mono" }, `MMSI ${mmsi} \u00b7 flag ${flag} \u00b7 ${type}`),
  );
}

function scoreSection(detail: CaseDetail): HTMLElement {
  const hero = h(
    "div",
    { class: "score-hero" },
    detail.score === null ? "\u2014" : fmtInt(detail.score),
    h(
      "small",
      null,
      detail.score === null ? "not scored in this snapshot" : "risk score of 100",
    ),
  );

  const row = h(
    "div",
    null,
    hero,
    detail.score === null ? riskChip(null, null) : riskChip(detail.band, detail.score),
  );
  row.style.display = "flex";
  row.style.alignItems = "baseline";
  row.style.justifyContent = "space-between";
  row.style.gap = "var(--sp-3)";

  return h("div", { class: "rail-section" }, row);
}

function sanctionsBanner(sanctions: Sanctions): HTMLElement {
  const entry = sanctions.entry?.trim() || "Name match";

  return h(
    "div",
    { class: "rail-section" },
    h(
      "div",
      { class: "banner is-error" },
      h("strong", null, "Possible sanctions list match"),
      h("div", null, `${entry} \u00b7 needs analyst confirmation`),
      h("div", { class: "sub" }, `Source: ${sanctions.source}`),
    ),
  );
}

function factorRow(factor: CaseDetail["score_factors"][number]): HTMLElement {
  const pct = Math.min(100, Math.max(0, factor.points * 4));
  const barFill = h("i", null);
  barFill.style.width = `${pct}%`;

  return h(
    "div",
    { class: "factor" },
    h("div", { class: "label" }, factor.label),
    h(
      "div",
      { class: "value" },
      h("div", null, factor.value),
      h("div", { class: "sub" }, `+${fmtInt(factor.points)} points`),
    ),
    h("div", { class: "note" }, factor.note),
    h("div", { class: "bar is-high" }, barFill),
  );
}

interface CandidateGroup {
  count: number;
  reason: string;
  verdict: Candidate["verdict"];
}

/** Group detections by reason *and* verdict so the two outcomes never merge. */
function groupCandidates(candidates: Candidate[]): CandidateGroup[] {
  const groups = new Map<string, CandidateGroup>();

  for (const candidate of candidates) {
    const key = `${candidate.verdict}\u0000${candidate.reason}`;
    const group = groups.get(key);
    if (group) {
      group.count += 1;
    } else {
      groups.set(key, {
        count: 1,
        reason: candidate.reason,
        verdict: candidate.verdict,
      });
    }
  }

  return [...groups.values()].sort(
    (a, b) =>
      b.count - a.count ||
      a.reason.localeCompare(b.reason) ||
      a.verdict.localeCompare(b.verdict),
  );
}

function detectionLine(group: CandidateGroup): HTMLElement {
  const noun = group.count === 1 ? "detection" : "detections";
  return h(
    "div",
    { class: "sub" },
    `${fmtInt(group.count)} radar ${noun}: ${group.reason}`,
  );
}

function whySection(detail: CaseDetail): HTMLElement {
  const body = h("div", { class: "rail-section" }, h("h3", null, reasonHeading(detail.status)));

  if (detail.score_factors.length > 0) {
    body.append(...detail.score_factors.map(factorRow));
  } else if (detail.candidates.length === 0) {
    body.append(h("p", { class: "sub" }, "No radar detection was close enough to test."));
  } else {
    body.append(...groupCandidates(detail.candidates).map(detectionLine));
  }

  body.append(
    h(
      "p",
      { class: "sub" },
      `Speed cap ${detail.speed_cap_kn} kn (${detail.speed_basis}). ` +
        `Straight-line distance between the two pings: ${detail.straight_line_kn} kn average.`,
    ),
  );

  return body;
}

function sourcesSection(detail: CaseDetail): HTMLElement {
  const rows = detail.sources.map((source) =>
    h(
      "div",
      { class: "sub" },
      h("strong", null, source.name),
      ` ${source.dataset} \u00b7 accessed ${source.accessed}`,
    ),
  );
  return h("div", { class: "rail-section" }, h("h3", null, "Data sources"), ...rows);
}

function caveatsSection(detail: CaseDetail): HTMLElement {
  return h(
    "div",
    { class: "rail-section" },
    h("h3", null, "Caveats"),
    h("ul", null, ...detail.caveats.map((caveat) => h("li", null, caveat))),
  );
}

/** Full case file rail. The page supplies the scrolling rail container. */
export function caseRail(detail: CaseDetail): HTMLElement {
  const inner = h("div", { class: "rail-inner" }, caseHead(detail), scoreSection(detail));

  if (detail.sanctions !== null && detail.sanctions.match) {
    inner.append(sanctionsBanner(detail.sanctions));
  }

  inner.append(
    whySection(detail),
    sourcesSection(detail),
    caveatsSection(detail),
  );
  return inner;
}
