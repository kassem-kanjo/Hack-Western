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
  | "sea1_kind"
  | "sea1_hours"
  | "sea1_days"
  | "sea1_km"
  | "cluster_ships"
  | "cluster_hours"
  | "cluster_kn"
  | "met"
  | "dark_alert";

export interface PresentCaption {
  headline: string;
  line: string;
}

export interface PresentScene extends PresentCaption {
  route: PresentRouteName;
  /**
   * The token this scene cannot be told without. When the API answers and that
   * token is absent (no such story in the data), the scene is skipped, not shown
   * with a guess.
   */
  requires?: PresentToken;
  /** Focus the strongest current story on the watch screen (as the cold open does). */
  alert?: "strongest";
  /** Open this Hormuz story card; absent means the strait view. */
  card?: HormuzCardKey;
  /**
   * Where the caption sits, chosen so it covers no proof on this screen:
   * "bottom" centre (default, Hormuz), "top-right" over the strait's empty sea (Hormuz
   * cards), "map-top-left" over the watch map's empty north-west (keeps the Live chip,
   * the status bar, the reach circle and the figures panel clear), "camera-top-left" over the top-left
   * corner of the patrol camera (the alarm, the figures and the Kill/Overload buttons stay clear).
   */
  dock?: "bottom" | "bottom-left" | "top-right" | "map-top-left" | "camera-top-left";
  /**
   * The element the tour outlines while this caption is up. CSS only; the tour
   * never clicks it.
   */
  highlight?: "patrol-kill";
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
        // Top-right keeps the caption off the two comparison boxes, which sit mid-map.
        dock: "top-right",
        headline: "The chart shows the change since the war began on 28 Feb",
        line: "On the map: Hormuz in red, the Gulf of Oman next door in grey. The chart on the right compares them.",
      },
    ],
  },
  {
    id: "cards",
    scenes: [
      {
        route: "hormuz",
        card: "sea1",
        dock: "bottom-left",
        requires: "sea1_hours",
        headline: "{sea1_kind} switched off for {sea1_hours} hours",
        line: "{sea1_days} days after the war began. It came back {sea1_km} km away.",
        fallback: {
          headline: "A ship switched off its transponder",
          line: "It switched off its transponder after the war began.",
        },
      },
      {
        route: "hormuz",
        card: "cluster",
        dock: "bottom-left",
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
        dock: "map-top-left",
        // Only shown when the data has a ship that switched off; never over a stopped ship.
        requires: "dark_alert",
        headline: "Our own receivers caught a ship switching off",
        line: "Here is where it could have gone, and the {met} ships it could have met.",
        fallback: {
          line: "Here is where it could have gone while its transponder was off.",
        },
      },
    ],
  },
  {
    id: "patrol",
    scenes: [
      {
        route: "patrol",
        dock: "camera-top-left",
        highlight: "patrol-kill",
        // Only what the screen shows: the presenter presses Kill; nothing here claims it is off.
        headline: "A camera lookout on a QNX Raspberry Pi.",
        line: "Press the highlighted 'Kill the AI detector': the safety core keeps watching and says 'lookout blind' instead of going quiet.",
      },
    ],
  },
  {
    id: "close",
    scenes: [
      {
        route: "hormuz",
        dock: "bottom",
        headline: "The chart shows the change since the war began on 28 Feb",
        line: "Physics says what's possible, lists say who matters, and the edge unit catches ships that switch off.",
      },
    ],
  },
];
