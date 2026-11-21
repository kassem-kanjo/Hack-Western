import { fmtUtcFull } from "../format";
import { h } from "./dom";
import { mountSearchBox } from "./SearchBox";

export type ConsoleMode = "SNAPSHOT" | "LIVE" | "ERROR";

/** Default live region for the Live tab (the one with the most data). */
export const DEFAULT_LIVE_REGION = "BALTIC_WATCH";

/**
 * Header: brand, the two places (Watch, Patrol unit), Present, and one mode badge in the
 * same slot on every screen. The case file stays reachable by clicking a case rather than
 * as a top-level tab, per design/v2/00-ia.md. Global and Live left the nav with the V3
 * Watch screen (#86); their routes and files are still there for presentation mode.
 *
 * `caseHref` and `demoHref` are accepted but ignored: old screens still pass them, and
 * removing the props there is outside this change. No header renders a "Case file" tab or
 * a "Start demo" button.
 */
export function topBar(opts: {
  active: "overview" | "case" | "live" | "patrol" | "watch" | "hormuz";
  mode: ConsoleMode;
  generatedAt: string | null;
  caseHref?: string | null;
  demoHref?: string | null;
  liveRegion?: string;
}): HTMLElement {
  const tab = (label: string, href: string, isActive: boolean): HTMLElement =>
    h("a", { class: `tab${isActive ? " is-active" : ""}`, href }, label);

  // Evidence is the old Global funnel and the case files behind it; the radar
  // picture of the strait has its own tab because it is the strongest single view.
  const evidenceActive = opts.active === "overview" || opts.active === "case";
  const tabs = [
    tab("Hormuz", "#/hormuz", opts.active === "hormuz"),
    tab("Watch", "#/watch", opts.active === "watch"),
    tab("Evidence", "#/evidence", evidenceActive),
    tab("Patrol unit", "#/patrol", opts.active === "patrol"),
  ];

  const modeClass = opts.mode.toLowerCase();
  const modeText =
    opts.mode === "SNAPSHOT"
      ? opts.generatedAt !== null
        ? `SNAPSHOT ${fmtUtcFull(opts.generatedAt)}`
        : "SNAPSHOT"
      : opts.mode;

  // Presentation mode (design/v2/04-present.png). The click is handled by
  // document delegation in present.ts, because this bar is rebuilt on navigation.
  const present = h(
    "button",
    {
      class: "present-btn",
      type: "button",
      title: "Start the guided 3-minute walkthrough",
      "aria-label": "Present: start the guided walkthrough",
    },
    "Present",
    h("kbd", { class: "kbd" }, "P"),
  );

  return h(
    "header",
    { class: opts.active === "watch" ? "topbar is-watch" : "topbar" },
    h("span", { class: "brand" }, "Dark Fleet"),
    h("nav", { class: "tabs" }, ...tabs),
    /* [search #183] one mount; the field's behaviour lives in SearchBox.ts. */
    mountSearchBox(),
    h("span", { class: "topbar-spacer" }),
    present,
    h(
      "span",
      { class: `mode-badge is-${modeClass}` },
      h("span", { class: "dot" }),
      ` ${modeText}`,
    ),
  );
}
