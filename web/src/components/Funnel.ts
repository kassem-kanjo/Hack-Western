import { fmtInt } from "../format";
import { h } from "./dom";

interface Row {
  cls: string;
  label: string;
  value: number;
}

/** One row: label, proportional bar, value. Bars have a 2% floor so none vanish. */
function funnelRow(row: Row, total: number): HTMLElement {
  const raw = total > 0 ? (row.value / total) * 100 : 0;
  const pct = Math.round(Math.min(100, Math.max(2, raw)) * 10) / 10;

  const fill = h("i", null);
  fill.style.width = `${pct}%`;

  return h(
    "div",
    { class: `funnel-row ${row.cls}` },
    h("span", { class: "label" }, row.label),
    h("div", { class: "funnel-bar" }, fill),
    h("span", { class: "funnel-val" }, fmtInt(row.value)),
  );
}

/** Horizontal funnel from every scanned gap down to the unexplained ones. */
export function funnel(f: {
  gapsScanned: number;
  withRadar: number;
  unexplained: number;
}): HTMLElement {
  const rows: Row[] = [
    { cls: "is-all", label: "Gaps scanned", value: f.gapsScanned },
    { cls: "is-radar", label: "With radar nearby", value: f.withRadar },
    { cls: "is-unexplained", label: "Unexplained", value: f.unexplained },
  ];

  return h(
    "section",
    { class: "rail-section" },
    h("h3", null, "From gap to unexplained"),
    ...rows.map((row) => funnelRow(row, f.gapsScanned)),
    h(
      "p",
      { class: "funnel-note" },
      "Radar nearby = a satellite detection inside the ship's possible reach. " +
        "Unexplained = not ruled out by speed or by the satellite's pass time.",
    ),
  );
}
