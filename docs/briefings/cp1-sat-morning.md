# CP1 briefing: Saturday morning (final, Sat 21 Nov, 08:50 ET)

## In one line
Overnight we turned "dark ships" into a physics-based evidence tool with three theatres, real findings in
our own data, an honest self-test, and a working console with a presentation mode.

## What it is now (plain words)
1. **Hormuz (satellite radar):** on a typical day about **41%** of vessels radar sees around the strait have
   no matching location broadcast (median of 59 well-covered days, Aug 15–Nov 15). The public dark-ship
   record logs almost none of this inside the strait.
2. **Live (our own recording since Fri 23:18):** every ship our receivers hear in the Baltic (~4,000 ships),
   Malaysia's EOPL (~800) and the Black Sea, checked by physics:
   - **GPS spoofing near St Petersburg:** 4 unrelated ships each jumped 40-51 km off their route to the same
     point on land (Vsevolozhsky district, OpenStreetMap-checked) and back. 784 port false alarms and one
     river-port false alarm were removed by tightening the physics.
   - **Sanctions by IMO:** at least 21 sanctioned/shadow-fleet ships seen in the Baltic in ~9 h (e.g. tanker
     GESTIA, listed as "Jaldhara"); off Singapore, shadow-fleet tanker ROCK and sanctioned tanker SHUSHA.
   - Ship-to-ship transfers detected (so far routine refuelling in anchorages; the tool says so).
3. **Honest self-test:** radar near a dark ship is **no better than chance** (3 pre-registered tests), so we
   never accuse on radar alone; locating a dark ship works (AUC 0.87).
4. **Patrol unit (QNX-ready):** edge safety core, AI restarts in 0.75 s after being killed.

## What's on screen now
Hormuz opening view (heat map, 41%), live map (calm markers, vessel cards with sanctions entries),
event detail with animated physics proof, presentation mode (8 steps, live numbers), README + architecture.

## How we got here (process)
~60 merged PRs, ~30 closed issues, almost all code written by DeepSeek coders and checked by Chief on real
data or in a real browser. Two independent Opus reviews caught a fake opener, a whitewashed hotspot, ferries
counted as "impossible", and stale numbers; real-data checks caught 784 false spoofing alarms (ports) and a
river-port false positive (Neva). Process audits every 30 min (docs/process-audit.md).

## 🟥 Decisions / actions for Kassem (≈ 10 min total)
1. **Approve the story** (docs/demo-script.md v2): Hormuz radar → live spoofing → sanctions → honest self-test.
2. **Vultr sign-up** (free MLH credits) → live URL + recorder off the laptop.
3. **MLH codes:** claim Entire + GoDaddy on the MLH prizes page; paste into .env.
4. **QNX Pi:** ask around before 12:00, or we drop the QNX track.
5. **Rehearse with me** once at ~14:00 and once Sunday 07:00.

## Plan to Sunday (unchanged milestones)
Sat 09-15: CP2 fixes merged, Hormuz density polish, replay slider, deploy, Entire evidence → **CP3 15:00**
(Opus review + security). Sat 15-22: polish, rehearsal, video, Devpost draft. Sun 06:00 final snapshot →
submit by 08:30.
