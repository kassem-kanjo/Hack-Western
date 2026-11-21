/**
 * The six beats of the tour, as data.
 *
 * `route` is a *name* from PRESENT_ROUTES, never a literal hash: if another
 * screen is renamed, the tour is fixed in one place at the top of this file.
 * `headline` and `line` are what the room reads; the line carries `{token}`
 * placeholders that present.ts fills once, from the live APIs, when the tour
 * starts. `fallback` is a complete sentence without the number, so a missing
 * endpoint never puts "undefined" or a stale figure on screen.
 *
 * Beat 2 is conditional: it exists only when /api/hormuz/trend reports
 * regime.verdict "moves with event". Without that proof the tour skips it
 * entirely rather than claim a number we have not tested.
 */

/** Every screen the tour can visit, by name. Renamed routes change here only. */
export const PRESENT_ROUTES = {
  hormuz: "#/hormuz",
  watch: "#/watch",
  patrol: "#/patrol",
  /** There is no #/evidence route yet; the watch screen carries the evidence. */
  evidence: "#/watch",
} as const;

export type PresentRouteName = keyof typeof PRESENT_ROUTES;

/** "#/watch/alert/<id>": the focus view for one alert. */
export function watchAlertRoute(alertId: string): string {
  return `${PRESENT_ROUTES.watch}/alert/${encodeURIComponent(alertId)}`;
}

/** Numbers present.ts can fill into a caption. */
export type PresentToken =
  | "hormuz_share_pct"
  | "ships_now"
  | "areas"
  | "alerts"
  | "spoof_ships"
  | "offset_min"
  | "offset_max"
  | "recovery"
  | "null_ratio"
  | "hormuz_before"
  | "hormuz_after"
  | "did_pts";

/** The two lines the overlay shows for one beat. */
export interface PresentCaption {
  headline: string;
  line: string;
}

export interface PresentStep {
  /** Stable key, used for markup ids and to keep the presenter on the same beat. */
  id: string;
  /** Which screen the beat shows, resolved through PRESENT_ROUTES. */
  route: PresentRouteName;
  /**
   * Open one alert on the watch screen. The id is filled from the tour's one
   * /api/watch fetch; when it is missing the beat stays on the world view.
   */
  alert?: "spoof";
  /**
   * Show this beat only when a condition was proved at tour start. The lone
   * condition is the Hormuz trend verdict; unproved means the beat is gone.
   */
  requires?: "hormuz_trend";
  /** One sentence the room reads (<= 12 words). */
  headline: string;
  /** One supporting line under it (<= 18 words). */
  line: string;
  /** Replaces a field whose token has no live value; omit the fields with no token. */
  fallback?: Partial<PresentCaption>;
}

export const PRESENT_STEPS: readonly PresentStep[] = [
  {
    id: "radar",
    route: "hormuz",
    headline: "Radar sees ships the transponders don't",
    line: "On a typical day {hormuz_share_pct}% of radar-seen vessels here aren't broadcasting.",
    fallback: {
      line: "On a typical day, many radar-seen vessels here aren't broadcasting.",
    },
  },
  {
    id: "trend",
    route: "hormuz",
    requires: "hormuz_trend",
    headline: "Since the strait closed, dark ships doubled",
    line: "Not broadcasting went from {hormuz_before}% to {hormuz_after}%: {did_pts} points beyond the Gulf of Oman control.",
    fallback: {
      line: "Not broadcasting rose after the strait closed, and the Gulf of Oman control did not move the same way.",
    },
  },
  {
    id: "live",
    route: "watch",
    headline: "Our own receivers, live",
    line: "{ships_now} ships heard right now across {areas} areas; {alerts} alerts pass our physics checks.",
    fallback: {
      line: "Ships are being heard right now across our listening areas; every alert passed our physics checks.",
    },
  },
  {
    id: "spoof",
    route: "watch",
    alert: "spoof",
    headline: "{spoof_ships} ships at one point on land",
    line: "Ships can't jump {offset_min}-{offset_max} km and back. Their positions were faked.",
    fallback: {
      headline: "Several ships at one point on land",
      line: "Ships can't jump across the sea and back in minutes. Their positions were faked.",
    },
  },
  {
    id: "patrol",
    route: "patrol",
    headline: "AI is a sensor, not a judge",
    line: "Kill the detector: the safety core stays up and recovers in {recovery}.",
    fallback: {
      line: "Kill the detector: the safety core stays up and keeps checking.",
    },
  },
  {
    id: "close",
    route: "evidence",
    headline: "We tested ourselves",
    line: "Radar near a dark ship was {null_ratio}x chance, so we never accuse on radar alone.",
    fallback: {
      line: "Radar near a dark ship was no better than chance, so we never accuse on radar alone.",
    },
  },
];
