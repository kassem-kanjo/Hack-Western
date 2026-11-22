import "../styles/guide.css";
import { h } from "./dom";

/** Screens that carry a guidance line. The name is also the localStorage key suffix. */
export type GuideScreen = "hormuz" | "watch" | "patrol";

const KEY_PREFIX = "darkfleet.guide.";

function isDismissed(screen: GuideScreen): boolean {
  try {
    return window.localStorage.getItem(KEY_PREFIX + screen) === "dismissed";
  } catch {
    // A blocked store (private mode) means the line shows every time; never crash the screen.
    return false;
  }
}

function rememberDismissed(screen: GuideScreen): void {
  try {
    window.localStorage.setItem(KEY_PREFIX + screen, "dismissed");
  } catch {
    // Nothing to do: the line comes back on the next visit.
  }
}

/**
 * One plain line at the top of a screen: what it shows and the one thing to do.
 * The × hides it for good on this browser. Returns an element that is already hidden
 * when the visitor dismissed it before, so callers can always append it.
 */
export function guideLine(screen: GuideScreen, text: string): HTMLElement {
  const line = h(
    "aside",
    { class: `guide-line is-${screen}`, role: "note", hidden: isDismissed(screen) },
    h("p", { class: "guide-text" }, text),
    h(
      "button",
      { class: "guide-close", type: "button", "aria-label": "Hide this line", title: "Hide this line" },
      "×",
    ),
  );
  line.querySelector<HTMLButtonElement>(".guide-close")?.addEventListener("click", () => {
    rememberDismissed(screen);
    line.hidden = true;
  });
  return line;
}
