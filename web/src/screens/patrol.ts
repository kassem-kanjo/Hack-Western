import { topBar } from "../components/TopBar";
import { h } from "../components/dom";

/**
 * Patrol unit tab. The edge box is not connected to this console yet, and no status
 * endpoint exists, so this screen explains the design and says plainly that it goes
 * live only when a device is connected. Figures here are design targets, not measurements.
 */
export function renderPatrol(app: HTMLElement): void {
  const facts: [string, string][] = [
    ["Safety core", "A small, separate control loop decides what the boat is allowed to do. The AI only suggests."],
    ["Recovery target", "0.75 s to fall back to the safe loop after a fault (design target, not yet measured)."],
    ["Frame budget", "10 ms per camera frame (design target, not yet measured)."],
    ["Status", "Live when the device is connected. Nothing is connected to this console today."],
  ];

  const rows = facts.map(([label, value]) =>
    h(
      "div",
      { class: "patrol-row" },
      h("div", { class: "patrol-label" }, label),
      h("div", { class: "patrol-value" }, value),
    ),
  );

  app.replaceChildren(
    topBar({
      active: "patrol",
      mode: "SNAPSHOT",
      generatedAt: null,
      caseHref: null,
      demoHref: null,
    }),
    h(
      "main",
      { class: "main" },
      h(
        "section",
        { class: "stage patrol" },
        h("h2", { class: "patrol-title" }, "Patrol unit"),
        h(
          "p",
          { class: "patrol-lede" },
          "The patrol unit is the camera box that sits on a patrol boat. It checks what the camera sees against what AIS says.",
        ),
        h("div", { class: "patrol-table" }, ...rows),
      ),
    ),
  );
}
