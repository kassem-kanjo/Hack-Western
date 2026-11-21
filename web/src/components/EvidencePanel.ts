/**
 * "What the evidence can and can't say": the validation checks from /api/stats
 * as three plain readings, then the one-sentence verdict.
 *
 * The panel fetches its own data so the overview stays a synchronous paint.
 * Rows whose headline number is null are omitted rather than printed as a
 * dash, and the whole panel is dropped when the API has no validation block.
 */

import type { Validation } from "../api";
import { fetchStats } from "../api";
import { h } from "./dom";

export interface EvidencePanelHandle {
  element: HTMLElement;
  destroy(): void;
}

function fmt2(n: number): string {
  return n.toFixed(2);
}

/** "(0.84–0.90)" from a nullable interval; null when either end is missing. */
function fmtCi(ci: [number, number] | null): string | null {
  return ci === null ? null : `(${fmt2(ci[0])}\u2013${fmt2(ci[1])})`;
}

interface Row {
  label: string;
  value: string;
  /** Plain word between the number and the interval, e.g. "chance". */
  after?: string;
  ci: [number, number] | null;
  note: string;
}

/** One reading: big mono number on the headline, one plain sentence under it. */
function evidenceRow(row: Row): HTMLElement {
  const ci = fmtCi(row.ci);

  return h(
    "div",
    { class: "evidence-row" },
    h(
      "div",
      { class: "evidence-head" },
      row.label,
      h("span", { class: "evidence-num" }, row.value),
      row.after !== undefined ? ` ${row.after}` : null,
      ci !== null ? h("span", { class: "evidence-ci" }, ` ${ci}`) : null,
    ),
    h("p", { class: "evidence-note" }, row.note),
  );
}

/** Only rows with a usable headline number are printed. */
function evidenceRows(validation: Validation): HTMLElement[] {
  const rows: HTMLElement[] = [];

  if (validation.bridge_auc !== null) {
    rows.push(
      evidenceRow({
        label: "Locates ships: AUC ",
        value: fmt2(validation.bridge_auc),
        ci: validation.bridge_auc_ci,
        note:
          "the true position beats a decoy 50-150 km away " +
          `${Math.round(validation.bridge_auc * 100)}% of the time`,
      }),
    );
  }

  if (validation.null_ratio !== null) {
    rows.push(
      evidenceRow({
        label: "Radar near a dark ship: ",
        value: `${fmt2(validation.null_ratio)}\u00d7`,
        after: "chance",
        ci: validation.null_ci,
        note: "no better than chance in 3 months of data",
      }),
    );
  }

  if (validation.holdout_ratio !== null) {
    rows.push(
      evidenceRow({
        label: "Held-out months: ",
        value: `${fmt2(validation.holdout_ratio)}\u00d7`,
        ci: validation.holdout_ci,
        note: "too few cases to say more",
      }),
    );
  }

  return rows;
}

/** Fill the panel body; false when there is nothing worth showing. */
function paint(body: HTMLElement, validation: Validation | null): boolean {
  if (!validation) return false;

  const rows = evidenceRows(validation);
  const verdict = validation.verdict?.trim() ?? "";

  if (rows.length === 0 && verdict === "") return false;

  const children: (HTMLElement | string)[] = [...rows];
  if (verdict !== "") children.push(h("p", { class: "evidence-verdict" }, verdict));
  children.push(h("p", { class: "evidence-method" }, "Method: docs/validation.md"));

  body.replaceChildren(...children);
  return true;
}

/** The overview rail panel; the caller inserts `element` under the funnel. */
export function evidencePanel(): EvidencePanelHandle {
  const body = h(
    "div",
    { class: "evidence-body" },
    h("p", { class: "evidence-note" }, "Loading validation..."),
  );
  const element = h(
    "section",
    { class: "rail-section evidence-panel" },
    h("h3", null, "What the evidence can and can't say"),
    body,
  );

  const abort = new AbortController();

  void fetchStats(abort.signal)
    .then((stats) => {
      // A navigation may have removed the panel while the request was in flight.
      if (abort.signal.aborted || !element.isConnected) return;
      if (!paint(body, stats.validation)) element.remove();
    })
    .catch(() => {
      // Missing stats are not a console failure: the overview still works.
      if (!abort.signal.aborted) element.remove();
    });

  return {
    element,
    destroy(): void {
      abort.abort();
    },
  };
}
