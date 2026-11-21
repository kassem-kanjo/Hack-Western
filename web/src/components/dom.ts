/**
 * Minimal DOM builder. Nodes are created with createElement and strings become
 * text nodes, so snapshot data is never parsed as markup.
 */

/**
 * Create an element.
 *
 * - `class` sets className.
 * - `true` writes the attribute with an empty value, `false`/`null` skip it.
 * - Everything else is set with setAttribute.
 * - Strings become text nodes; `null`, `undefined` and `false` are dropped.
 */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: Record<string, string | number | boolean | null> | null,
  ...children: (Node | string | null | undefined | false)[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);

  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === false) continue;
      if (key === "class") {
        el.className = String(value);
        continue;
      }
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }

  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    el.append(typeof child === "string" ? document.createTextNode(child) : child);
  }

  return el;
}
