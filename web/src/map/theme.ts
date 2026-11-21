/**
 * Bridge between the CSS design tokens and MapLibre paint values.
 *
 * MapLibre cannot read CSS custom properties, so callers snapshot them once at
 * mount time. Keeping the palette in tokens.css (rather than hard-coding hex
 * values in the map modules) means the map and the rest of the console can
 * never drift apart. Fallbacks keep the style valid if a token is missing.
 */

export function themeColor(variable: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(variable)
    .trim();
  return value || fallback;
}

/**
 * "#RRGGBB" + alpha -> "rgba(r, g, b, a)".
 *
 * Used for the translucent map fills and for dimming ruled-out features
 * without introducing a second token. Non-hex input (for example an existing
 * rgba() token) is returned untouched so it stays a valid CSS colour.
 */
export function rgba(hex: string, alpha: number): string {
  const raw = hex.trim().replace(/^#/, "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return hex;
  const n = Number.parseInt(full, 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  const a = Math.min(1, Math.max(0, alpha));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
