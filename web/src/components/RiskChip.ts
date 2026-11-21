import type { Band } from "../api";
import { fmtInt } from "../format";
import { h } from "./dom";

const BAND_CLASS: Record<Band, string> = {
  high: "is-high",
  medium: "is-medium",
  low: "is-low",
};

const BAND_LABEL: Record<Band, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

/** Risk band + score as a chip, or "Not scored" when either is missing. */
export function riskChip(band: Band | null, score: number | null): HTMLElement {
  if (band === null || score === null) {
    return h("span", { class: "chip is-none" }, "Not scored");
  }
  return h(
    "span",
    { class: `chip ${BAND_CLASS[band]}` },
    `${BAND_LABEL[band]} ${fmtInt(score)}`,
  );
}
