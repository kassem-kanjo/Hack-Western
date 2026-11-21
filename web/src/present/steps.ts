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
  | "alerts"
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
  /** Hash route to show for this beat. */
  route: string;
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
    id: "world",
    route: "#/",
    headline: "Every big ship must broadcast where it is",
    line: "Around Hormuz, radar sees ships daily; on a typical day, about {hormuz_share_pct}% aren't broadcasting.",
    proof: "{hormuz_share_pct}% dark · {hormuz_days_used} days measured",
    fallback: {
      line: "Around Hormuz, radar sees ships daily; on a typical day, many aren't broadcasting.",
      proof: "radar sees ships · many dark",
    },
  },
  {
    id: "hormuz-rule",
    route: "#/",
    headline: "Their rule can't see inside the strait",
    line: "Global Fishing Watch logged almost nothing inside the strait in six months; their rule only counts ships far offshore.",
    proof: "GFW offshore rule · strait inside",
  },
  {
    id: "baltic-live",
    route: "#/live/BALTIC_WATCH",
    headline: "So we built our own, live since Friday",
    line: "We've recorded every ship in six shadow-fleet hotspots; the Baltic alone is {baltic_ships} ships and {baltic_events} findings.",
    proof: "live map · coverage line",
    fallback: {
      line: "We've recorded every ship in six shadow-fleet hotspots; the Baltic recording is live on the map.",
    },
  },
  {
    id: "spoof",
    route: "#/live/BALTIC_WATCH/event/0",
    headline: "Physics catches what statements can't",
    line: "{spoof_ships} ships jumped from their route to one point on land near St Petersburg, and back. Physics says spoofed.",
    proof: "{spoof_ships} ships · one land point",
    fallback: {
      line: "Several ships jumped from their route to one point on land near St Petersburg, and back. Physics says spoofed.",
      proof: "several ships · one land point",
    },
  },
  {
    id: "clone-correction",
    route: "#/live/BALTIC_WATCH/event/0",
    headline: "Our own detector was wrong; physics corrected it",
    line: "What looked like cloned identities was really the spoof point, so we relabelled them; the tool corrects itself.",
    proof: "per-ship jump in and out",
  },
  {
    id: "watchlist",
    route: "#/live/BALTIC_WATCH/event/0",
    headline: "{alerts} sanctioned ships are in the Baltic now",
    line: "We check every IMO number against sanctions lists; {alerts} sanctioned or shadow-fleet ships are sailing here now.",
    proof: "red watchlist tag · SAPFIR",
    fallback: {
      headline: "Sanctioned ships are in the Baltic now",
      line: "We check every IMO number against sanctions lists; the red tag marks sanctioned or shadow-fleet ships sailing here now.",
    },
  },
  {
    id: "validation",
    route: "#/",
    headline: "We tested ourselves and don't accuse on radar",
    line: "Of {gaps_scanned} dark gaps, {radar_nearby} had radar nearby; physics cleared {cleared}, leaving {unexplained} unexplained. Radar was no better than chance. Location: {bridge_auc}.",
    proof: "funnel · {null_ratio}× · AUC {bridge_auc}",
    fallback: {
      line: "Of thousands of dark gaps, some had radar nearby and physics cleared most. Radar was no better than chance; location we do well.",
      proof: "funnel · radar no better than chance",
    },
  },
  {
    id: "close",
    route: "#/live/BALTIC_WATCH",
    headline: "We separate what physics proves from suspicion",
    line: "A tool for compliance teams and coast guards. Ask me about any dot on the map.",
    proof: "back to the live map",
  },
];
