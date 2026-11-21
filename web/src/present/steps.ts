/**
 * The eight beats of the 3:00 demo, as data.
 *
 * One record per beat from docs/demo-script.md. `route` is the hash route the
 * app should be showing; `headline` and `line` are what the room reads; `proof`
 * is the tiny presenter-facing label that names what the screen is proving.
 * Reordering the story is an edit to this array, not a rebuild of the overlay.
 *
 * A caption never hardcodes a number that comes from the recording: the text
 * carries `{token}` placeholders and present.ts fills them from the live API
 * when presentation starts. `fallback` is a complete sentence without the
 * number, used when an endpoint is missing or slow, so the room never reads
 * "undefined" or a stale figure.
 */

/** Numbers present.ts can fill into a caption. */
export type PresentToken =
  | "spoof_ships"
  | "hormuz_share_pct"
  | "hormuz_days_used"
  | "baltic_ships"
  | "baltic_events"
  | "alerts_at_least"
  | "window_h"
  | "watchlist_vessel"
  | "gaps_scanned"
  | "radar_nearby"
  | "cleared"
  | "unexplained"
  | "null_ratio"
  | "bridge_auc";

/** The three lines the overlay shows for one beat. */
export interface PresentCaption {
  headline: string;
  line: string;
  proof: string;
}

export interface PresentStep {
  /** Stable key, used only for markup ids and debugging. */
  id: string;
  /**
   * Hash route to show for this beat. It may carry a route token such as
   * `{watchlist_mmsi}`; present.ts fills those at run time and uses
   * `fallbackRoute` when the value is not known yet.
   */
  route: string;
  /** Route for a beat whose route token has no live value; omit when fixed. */
  fallbackRoute?: string;
  /** One sentence the room reads (<= 9 words). */
  headline: string;
  /** One supporting line under it (<= 22 words). */
  line: string;
  /** Short label naming the evidence already on screen. */
  proof: string;
  /** Replaces any field whose token has no live value; omit the fields with no token. */
  fallback?: Partial<PresentCaption>;
}

export const PRESENT_STEPS: readonly PresentStep[] = [
  {
    id: "hormuz",
    route: "#/hormuz",
    headline: "The fleet nobody declares",
    line: "Around Hormuz, on a typical day about {hormuz_share_pct}% of vessels radar sees have no matching broadcast. Some are small boats; many are not.",
    proof: "{hormuz_share_pct}% · {hormuz_days_used} well-covered days, Aug–Nov",
    fallback: {
      line: "Around Hormuz, many vessels radar sees each day have no matching broadcast. Some are small boats; many are not.",
      proof: "radar vs broadcasts · Aug–Nov",
    },
  },
  {
    id: "hormuz-rule",
    route: "#/hormuz",
    headline: "The public record barely sees it",
    line: "Global Fishing Watch logged one dark gap inside the strait from May to November: its rule only counts ships far offshore.",
    proof: "1 GFW gap event · separate query",
  },
  {
    id: "live",
    route: "#/live/BALTIC_WATCH",
    headline: "So we record it ourselves",
    line: "Since Friday night we've captured every ship our receivers hear in three hotspots. The Baltic: {baltic_ships} ships in {window_h} hours.",
    proof: "live map · coverage line",
    fallback: {
      line: "Since Friday night we've captured every ship our receivers hear in three hotspots, starting with the Baltic.",
    },
  },
  {
    id: "spoof",
    route: "#/live/BALTIC_WATCH/kind/spoof",
    headline: "Physics catches what statements can't",
    line: "{spoof_ships} unrelated ships each jumped tens of kilometres to one point on land near St Petersburg, and back. Their positions were falsified, or a shared device is wrong.",
    proof: "{spoof_ships} ships · one point on land",
    fallback: {
      line: "Several unrelated ships each jumped tens of kilometres to one point on land near St Petersburg, and back.",
      proof: "several ships · one point on land",
    },
  },
  {
    id: "self-check",
    route: "#/live/BALTIC_WATCH/kind/spoof",
    headline: "And we check ourselves",
    line: "An early version flagged a river port in St Petersburg. We tightened the physics and it disappeared. Every alert says why.",
    proof: "per-ship jump in and out",
  },
  {
    id: "watchlist",
    route: "#/live/BALTIC_WATCH/vessel/{watchlist_mmsi}",
    fallbackRoute: "#/live/BALTIC_WATCH",
    headline: "At least {alerts_at_least} listed ships in {window_h} hours",
    line: "Every broadcast IMO number is checked against sanctions lists: at least {alerts_at_least} sanctioned or shadow-fleet ships seen here in the last {window_h} hours.",
    proof: "vessel card · {watchlist_vessel}",
    fallback: {
      headline: "Sanctioned ships, seen in our recording",
      line: "Every broadcast IMO number is checked against sanctions lists; the red tag marks sanctioned or shadow-fleet ships we heard.",
      proof: "red watchlist tag · vessel card",
    },
  },
  {
    id: "validation",
    route: "#/",
    headline: "We never accuse on radar alone",
    line: "We tested our own method: radar near a dark ship was no better than chance. Physics is what we trust.",
    proof: "evidence panel · {null_ratio}× chance",
    fallback: { proof: "evidence panel" },
  },
  {
    id: "close",
    route: "#/live/BALTIC_WATCH",
    headline: "What physics proves, apart from what looks suspicious",
    line: "For compliance teams and coast guards. Ask me about any dot on the map.",
    proof: "back to the live map",
  },
];
