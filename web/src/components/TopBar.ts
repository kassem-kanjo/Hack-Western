import { fmtUtcFull } from "../format";
import { h } from "./dom";
import { introWillShow } from "./Intro";
import { mountSearchBox } from "./SearchBox";

export type ConsoleMode = "SNAPSHOT" | "LIVE" | "ERROR";

const TOUR_SEEN_KEY = "darkfleet.tourSeen";
const TOUR_HINT_MS = 8000;

/** True once per browser: marks the visit as seen, and returns false if storage is blocked. */
function isFirstVisit(): boolean {
  try {
    if (window.localStorage.getItem(TOUR_SEEN_KEY) !== null) return false;
    window.localStorage.setItem(TOUR_SEEN_KEY, "1");
    return true;
  } catch {
    return false;
  }
}

/**
 * The page's hint window, fixed once per page load. Screens rebuild the bar on their first
 * render, so a per-call check would hide the hint on the render that is actually visible.
 * The intro card is the first-visit greeting: when it shows, the hint stays off (the visit is
 * still marked seen, so a later visit gets neither).
 */
let tourHintEndsAt: number | null = null;

function tourHintRemainingMs(): number {
  if (tourHintEndsAt === null) {
    const firstVisit = isFirstVisit();
    tourHintEndsAt = firstVisit && !introWillShow() ? Date.now() + TOUR_HINT_MS : 0;
  }
  return tourHintEndsAt - Date.now();
}

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

  // The menu follows the demo story: Hormuz (why it matters), Watch (live stories), Patrol
  // unit (the edge lookout). Evidence (South Atlantic case files and our self-checks) is off
  // the menu because it is not part of that story; #/evidence still opens for Q&A.
  const tabs = [
    tab("Hormuz", "#/hormuz", opts.active === "hormuz"),
    tab("Watch", "#/watch", opts.active === "watch"),
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
  // The primary entry point for someone who has never seen the app (#254). P still starts it.
  const present = h(
    "button",
    {
      class: "present-btn is-primary",
      type: "button",
      title: "Follow one ship (press P)",
      "aria-label": "Follow one ship",
    },
    "Follow one ship",
    h("kbd", { class: "kbd" }, "P"),
  );

  // Shown on the first visit only, for 8 s from the page load. Rebuilt bars inside that
  // window show the rest of it; a later visit (flag set) shows none. Blocked storage: none.
  const tourWrap = h("span", { class: "tour-wrap" }, present);
  const hintLeft = tourHintRemainingMs();
  if (hintLeft > 0) {
    const hint = h(
      "span",
      { class: "tour-hint", role: "note" },
      "New here? Start the tour, or click around.",
    );
    tourWrap.append(hint);
    window.setTimeout(() => hint.remove(), hintLeft);
  }

  return h(
    "header",
    { class: opts.active === "watch" ? "topbar is-watch" : "topbar" },
    h("span", { class: "brand" }, "Dark Fleet"),
    h("nav", { class: "tabs" }, ...tabs),
    /* [search #183] one mount; the field's behaviour lives in SearchBox.ts. */
    mountSearchBox(),
    h("span", { class: "topbar-spacer" }),
    tourWrap,
    h(
      "span",
      { class: `mode-badge is-${modeClass}` },
      h("span", { class: "dot" }),
      ` ${modeText}`,
    ),
  );
}
