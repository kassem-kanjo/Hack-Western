import { fmtUtcFull } from "../format";
import { h } from "./dom";

export type ConsoleMode = "SNAPSHOT" | "LIVE" | "ERROR";

/**
 * Header: brand, the two in-scope tabs, the snapshot region and the mode badge.
 * Tabs outside this issue's scope (rendezvous, live, edge unit) are not drawn.
 */
export function topBar(opts: {
  active: "overview" | "case";
  mode: ConsoleMode;
  generatedAt: string | null;
  caseHref: string | null;
}): HTMLElement {
  const overviewTab = h(
    "a",
    {
      class: `tab${opts.active === "overview" ? " is-active" : ""}`,
      href: "#/",
    },
    "Overview",
  );

  const caseTab =
    opts.caseHref !== null
      ? h(
          "a",
          {
            class: `tab${opts.active === "case" ? " is-active" : ""}`,
            href: opts.caseHref,
          },
          "Case file",
        )
      : h("span", { class: "tab is-disabled" }, "Case file");

  const modeClass = opts.mode.toLowerCase();
  const modeText =
    opts.mode === "SNAPSHOT"
      ? opts.generatedAt !== null
        ? `SNAPSHOT ${fmtUtcFull(opts.generatedAt)}`
        : "SNAPSHOT"
      : opts.mode;

  return h(
    "header",
    { class: "topbar" },
    h("span", { class: "brand" }, "Dark Fleet"),
    h("nav", { class: "tabs" }, overviewTab, caseTab),
    h("span", { class: "topbar-spacer" }),
    h(
      "span",
      { class: "chip-pill" },
      h("span", { class: "k" }, "region"),
      " Global snapshot",
    ),
    h(
      "span",
      { class: `mode-badge is-${modeClass}` },
      h("span", { class: "dot" }),
      ` ${modeText}`,
    ),
  );
}
