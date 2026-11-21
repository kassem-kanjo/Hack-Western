import { fmtUtcFull } from "../format";
import { h } from "./dom";

export type ConsoleMode = "SNAPSHOT" | "LIVE" | "ERROR";

/** Default live region for the Live tab (the one with the most data). */
export const DEFAULT_LIVE_REGION = "BALTIC_WATCH";

/**
 * Header (01-nav): brand, three places (Global, Live, Patrol unit), Present, and one mode
 * badge in the same slot on every screen. The case file stays reachable by clicking a case
 * rather than as a top-level tab, per design/v2/00-ia.md.
 *
 * `caseHref` and `demoHref` are accepted but ignored: old screens still pass them, and
 * removing the props there is outside this change. No header renders a "Case file" tab or
 * a "Start demo" button.
 */
export function topBar(opts: {
  active: "overview" | "case" | "live" | "patrol";
  mode: ConsoleMode;
  generatedAt: string | null;
  caseHref?: string | null;
  demoHref?: string | null;
  liveRegion?: string;
}): HTMLElement {
  const tab = (label: string, href: string, isActive: boolean): HTMLElement =>
    h("a", { class: `tab${isActive ? " is-active" : ""}`, href }, label);

  const liveHref = `#/live/${opts.liveRegion ?? DEFAULT_LIVE_REGION}`;
  const tabs = [
    tab("Global", "#/", opts.active === "overview" || opts.active === "case"),
    tab("Live", liveHref, opts.active === "live"),
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
    { class: "topbar" },
    h("span", { class: "brand" }, "Dark Fleet"),
    h("nav", { class: "tabs" }, ...tabs),
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
