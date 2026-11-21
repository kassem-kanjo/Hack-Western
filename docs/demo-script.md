# Demo script v2 (3:00, expo table) — the fleet nobody declares

After the CP2 Auditor review (Sat 08:00). Numbers in {braces} are read live by presentation mode from the
snapshot built on Sunday morning; never say a number that isn't on screen. One idea per beat.

| # | Time | Screen (route) | Say (plain, first person) | Proof on screen |
|---|---|---|---|---|
| 1 | 0:00 | `#/hormuz` | "Around the Strait of Hormuz, radar satellites see vessels every day. On a typical day about {hormuz_share_pct}% of them have no matching location broadcast. Some are small boats that don't have to broadcast; many are not." | hero %, daily chart, "{hormuz_days_used} well-covered days, Aug 15 – Nov 15" |
| 2 | 0:25 | `#/hormuz` (radar layer) | "The best public dark-ship record logs almost none of this inside the strait: its rule only counts ships far offshore, and the strait is too narrow. So the gap is real and nobody's watching it." | density layer; caption "1 GFW gap event inside the strait, May–Nov (separate query)" |
| 3 | 0:45 | `#/live/BALTIC_WATCH` | "So we record it ourselves. Since Friday night we've captured every ship our receivers hear in three hotspots. This is the Baltic: {baltic_ships} ships in the last {window_h} hours." | live map, hero number, coverage line |
| 4 | 1:05 | `#/live/BALTIC_WATCH/kind/spoof` | "Physics catches what statements can't. {spoof_ships} unrelated ships each jumped tens of kilometres off their route to the same point on land near St Petersburg, and back. Ships can't do that: their satellite positions were falsified, or a shared device is wrong." | spoof event: ships, out-and-back explainer, "on land" |
| 5 | 1:35 | same event, numbers | "And we check ourselves: an early version flagged a river port in St Petersburg. We tightened the physics and it disappeared. Every alert says why it's an alert." | per-ship jump in/out speeds |
| 6 | 1:55 | `#/live/BALTIC_WATCH/vessel/{alert_mmsi}` | "Every ship's broadcast IMO number is checked against sanctions lists: at least {alerts_at_least} sanctioned or shadow-fleet ships seen here in the last {window_h} hours. This one broadcasts as {alert_name}; the list knows it as {alert_listed_name}." | vessel card with list entry |
| 7 | 2:20 | `#/` funnel + "How sure are we?" | "We tested our own method. Radar near a dark ship turned out to be no better than chance, so we never accuse on radar alone. Physics is what we trust." | evidence panel |
| 8 | 2:40 | back to `#/live/BALTIC_WATCH` | "It's for compliance teams and coast guards: it separates what physics proves from what only looks suspicious. Ask me about any dot." | live map |

QNX sponsor table only (Sun 10:00): Patrol unit tab, kill the detector live, recovery about 0.15 s (warm standby detector, measured on the Pi; see docs/patrol-demo.md).

## Rules
- Rehearse on the **Sunday 06:00 snapshot** (counts and event order change as the recording grows).
- Wifi off for one full run (snapshot + offline basemap).
- Never say: "every big ship must broadcast" next to the radar share; "right now" for the watchlist;
  "six hotspots"; "six months" for the Aug–Nov chart.
