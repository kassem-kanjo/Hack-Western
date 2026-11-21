import { h } from "./dom";

function legendRow(swatchClass: string, text: string): HTMLElement {
  return h(
    "div",
    { class: "legend-row" },
    h("span", { class: `swatch ${swatchClass}` }),
    text,
  );
}

/** Symbol key for the map. The case view adds lens, radar and verdict marks. */
export function legend(kind: "overview" | "case"): HTMLElement {
  const rows = [
    legendRow("is-dot", "Ship broadcasting (AIS)"),
    legendRow("is-dash", "AIS gap: last ping to reappearance"),
  ];

  if (kind === "overview") {
    rows.push(legendRow("is-dash is-ruled", "Gap ruled out (grey)"));
  } else {
    rows.push(
      legendRow("is-lens", "Where it could have been (lens)"),
      legendRow("is-likely", "Most likely position (Brownian bridge, 2σ)"),
      legendRow("is-diamond", "Radar detection (SAR)"),
      legendRow("is-candidate", "Detection inside the lens: candidate"),
      legendRow("is-ruled-diamond", "Detection outside the lens: ruled out"),
    );
  }

  return h("div", { class: "legend" }, ...rows);
}
