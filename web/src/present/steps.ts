/**
 * The five beats of the tour, as data (docs/demo-script.md, v3).
 *
 * A beat is one idea; a beat with two scenes (the Hormuz cards) advances scene by
 * scene before the next beat. `route` is a name from PRESENT_ROUTES, never a literal
 * hash. Captions carry `{token}` placeholders that present.ts fills from the same
 * endpoint the screen reads, using the screen's own rounding and wording. `fallback`
 * is a complete sentence without the number, used until (or unless) that endpoint answers.
 */

import type { HormuzCardKey } from "./cardRequest";

export const PRESENT_ROUTES = {
  hormuz: "#/hormuz",
  watch: "#/watch",
  patrol: "#/patrol",
} as const;

export type PresentRouteName = keyof typeof PRESENT_ROUTES;

/** "#/watch/alert/<id>": the focus view for one alert. */
export function watchAlertRoute(alertId: string): string {
  return `${PRESENT_ROUTES.watch}/alert/${encodeURIComponent(alertId)}`;
}

/** Numbers present.ts can fill into a caption. */
export type PresentToken =
  | "hormuz_before"
  | "hormuz_after"
  | "did_pts"
  | "sea1_hours"
  | "sea1_days"
  | "sea1_km"
  | "cluster_ships"
  | "cluster_hours"
  | "cluster_kn"
  | "met"
  | "recovery";

export interface PresentCaption {
  headline: string;
  line: string;
}

export interface PresentScene extends PresentCaption {
  route: PresentRouteName;
  /** Focus the strongest current story on the watch screen (as the cold open does). */
  alert?: "strongest";
  /** Open this Hormuz story card; absent means the strait view. */
  card?: HormuzCardKey;
  /**
   * Where the caption sits, chosen so it covers no proof on this screen:
   * "bottom" centre (default), "top-right" over the strait's empty sea, "left" beside
   * the camera (the patrol panel and its Kill button stay clear).
   */
  dock?: "bottom" | "top-right" | "left";
  fallback?: Partial<PresentCaption>;
}

export interface PresentBeat {
  id: string;
  scenes: readonly PresentScene[];
}

export const PRESENT_BEATS: readonly PresentBeat[] = [
  {
    id: "finding",
    scenes: [
      {
        route: "hormuz",
        headline: "Satellite radar sees ships that aren't broadcasting",
        line: "Since the war began, {hormuz_after}% of radar-seen ships here aren't broadcasting their position, up from {hormuz_before}% before 28 Feb.",
        fallback: {
          line: "Since the war began, more radar-seen ships here aren't broadcasting their position than before 28 Feb.",
        },
      },
    ],
  },
  {
    id: "cards",
    scenes: [
      {
        route: "hormuz",
        card: "sea1",
        dock: "top-right",
        headline: "A shadow-fleet ship switched off for {sea1_hours} hours",
        line: "In the Gulf of Oman, {sea1_days} days after the strikes. It came back {sea1_km} km away.",
        fallback: {
          headline: "A shadow-fleet ship switched off its transponder",
          line: "It switched off its transponder in the Gulf of Oman, after the strikes.",
        },
      },
      {
        route: "hormuz",
        card: "cluster",
        dock: "top-right",
        headline: "{cluster_ships} ships switched off, then reappeared at one spot",
        line: "The trip needs {cluster_kn} knots, which these ships can't sail. Their positions look faked, so we don't rank them.",
        fallback: {
          headline: "Several ships switched off, then reappeared at one spot",
          line: "The trip is too fast for these ships. Their positions look faked, so we don't rank them.",
        },
      },
    ],
  },
  {
    id: "live",
    scenes: [
      {
        route: "watch",
        alert: "strongest",
        headline: "Our own receivers caught a ship switching off",
        line: "Here is where it could have gone, and the {met} ships it could have met.",
        fallback: {
          line: "Here is where it could have gone, and the ships it could have met.",
        },
      },
    ],
  },
  {
    id: "patrol",
    scenes: [
      {
        route: "patrol",
        dock: "left",
        headline: "AI is a sensor, not a judge",
        line: "Kill the detector: the safety core stays up and recovers in {recovery}.",
        fallback: {
          line: "Kill the detector: the safety core stays up and keeps checking.",
        },
      },
    ],
  },
  {
    id: "close",
    scenes: [
      {
        route: "watch",
        headline: "Built for compliance teams and coast guards",
        line: "Physics says what's possible, lists say who matters, and the edge unit catches ships that switch off.",
      },
    ],
  },
];
