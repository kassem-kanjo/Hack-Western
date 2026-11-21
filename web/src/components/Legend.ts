import type { CaseLayerGroup } from "../map/caseMap";
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

/** One layer of the case map: key, switch and a single plain line saying what it means. */
export interface CaseLayerRow {
  group: CaseLayerGroup;
  swatch: string;
  label: string;
  explain: string;
}

export const CASE_LAYERS: CaseLayerRow[] = [
  { group: "gap", swatch: "is-dash", label: "AIS gap", explain: "Last ping to reappearance. The ship said nothing in between." },
  { group: "lens", swatch: "is-lens", label: "Lens", explain: "Everywhere it could have been, given its top speed." },
  { group: "likely", swatch: "is-likely", label: "Most likely position", explain: "Brownian-bridge ellipse: where it probably was (2\u03c3)." },
  { group: "radar", swatch: "is-diamond", label: "Radar detections", explain: "Bright = inside the lens, could be this ship. Grey = too far, ruled out." },
  { group: "footprint", swatch: "is-footprint", label: "Reach outline", explain: "Outer limit of the lens over the whole gap." },
];

/** Case-map layer panel: each row switches a layer and says in one line what it shows. */
export function caseLayerPanel(onToggle: (group: CaseLayerGroup, visible: boolean) => void): HTMLElement {
  const rows = CASE_LAYERS.map((layer) => {
    const input = h("input", { type: "checkbox", checked: true, "aria-label": layer.label });
    input.addEventListener("change", () => onToggle(layer.group, input.checked));
    return h(
      "label",
      { class: "layer-row" },
      input,
      h("span", { class: `swatch ${layer.swatch}` }),
      h("span", { class: "layer-text" }, h("span", { class: "layer-name" }, layer.label), h("span", { class: "layer-explain" }, layer.explain)),
    );
  });
  return h("div", { class: "legend layer-panel" }, h("div", { class: "layer-head" }, "Map layers"), ...rows);
}
