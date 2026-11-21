import { fmtInt } from "../format";
import { h } from "./dom";

interface Kpi {
  cls: string;
  value: number;
  label: string;
}

/** Four headline numbers for the snapshot, in pipeline order. */
export function kpiStrip(k: {
  gapsScanned: number;
  withRadar: number;
  ruledOut: number;
  unexplained: number;
}): HTMLElement {
  const items: Kpi[] = [
    {
      cls: "is-gap",
      value: k.gapsScanned,
      label: "AIS gaps scanned (6 to 96 h, 3 months)",
    },
    {
      cls: "is-radar",
      value: k.withRadar,
      label: "Gaps with radar nearby",
    },
    {
      cls: "is-ruled",
      value: k.ruledOut,
      label: "Ruled out by physics or timing",
    },
    {
      cls: "is-unexplained",
      value: k.unexplained,
      label: "Still unexplained",
    },
  ];

  return h(
    "section",
    { class: "kpi-strip" },
    ...items.map((item) =>
      h(
        "div",
        { class: `kpi ${item.cls}` },
        h("div", { class: "num" }, fmtInt(item.value)),
        h("div", { class: "label" }, item.label),
      ),
    ),
  );
}
