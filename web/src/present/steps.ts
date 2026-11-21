/**
 * The eight beats of the 3:00 demo, as data.
 *
 * One record per beat from docs/demo-script.md. `route` is the hash route the
 * app should be showing; `headline` and `line` are what the room reads; `proof`
 * is the tiny presenter-facing label that names what the screen is proving.
 * Reordering the story is an edit to this array, not a rebuild of the overlay.
 */
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
}

export const PRESENT_STEPS: readonly PresentStep[] = [
  {
    id: "world",
    route: "#/",
    headline: "Every big ship must broadcast where it is",
    line: "Around Hormuz, radar sees ships daily; on a typical day, about four in ten aren't broadcasting.",
    proof: "median 41% dark · 59 of 84 days",
  },
  {
    id: "hormuz-rule",
    route: "#/",
    headline: "Their rule can't see inside the strait",
    line: "Global Fishing Watch logged one dark event inside the strait in six months; their rule only counts ships far offshore.",
    proof: "1 gap event in 6 months",
  },
  {
    id: "baltic-live",
    route: "#/live/BALTIC_WATCH",
    headline: "So we built our own, live since Friday",
    line: "We've recorded every ship in six shadow-fleet hotspots; the Baltic alone is 3,800 ships, tens of thousands of positions an hour.",
    proof: "live map · coverage line",
  },
  {
    id: "spoof",
    route: "#/live/BALTIC_WATCH/event/0",
    headline: "Physics catches what statements can't",
    line: "Five ships jumped 49 km from their route to one point on land near St Petersburg, and back. Physics says spoofed.",
    proof: "5 ships · 49 km · on land",
  },
  {
    id: "clone-correction",
    route: "#/live/BALTIC_WATCH/event/0",
    headline: "Our own detector was wrong; physics corrected it",
    line: "Two looked like clones. Physics showed the second position was the spoof point, so we relabelled them; the tool corrects itself.",
    proof: "per-ship jump in and out",
  },
  {
    id: "watchlist",
    route: "#/live/BALTIC_WATCH/event/0",
    headline: "Eighteen sanctioned ships are in the Baltic now",
    line: "We check every IMO number against sanctions lists; 18 sanctioned or shadow-fleet ships are sailing here now, including SAPFIR.",
    proof: "red watchlist tag · SAPFIR",
  },
  {
    id: "validation",
    route: "#/",
    headline: "We tested ourselves and don't accuse on radar",
    line: "Of 5,624 dark gaps, 109 had radar nearby; physics cleared 90. Radar was no better than chance. Location: 0.87.",
    proof: "funnel · 1.07× · AUC 0.87",
  },
  {
    id: "close",
    route: "#/live/BALTIC_WATCH",
    headline: "We separate what physics proves from suspicion",
    line: "A tool for compliance teams and coast guards. Ask me about any dot on the map.",
    proof: "back to the live map",
  },
];
