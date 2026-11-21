import { h } from "./dom";

/** Data attribution line. The only external link in the console. */
export function attribution(): HTMLElement {
  return h(
    "div",
    { class: "attribution" },
    "Powered by ",
    h(
      "a",
      {
        href: "https://globalfishingwatch.org",
        target: "_blank",
        rel: "noopener noreferrer",
      },
      "Global Fishing Watch",
    ),
    " \u00b7 Natural Earth",
  );
}
