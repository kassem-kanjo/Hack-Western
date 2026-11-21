# Plan v3: use every hour (written Sat 21 Nov 03:45 ET)

Deadline Sun 22 Nov 09:00 (Devpost locks). Build window: now → Sun 06:00 (~26 h), then freeze + submit.
Code by DeepSeek coders (lead-less, Chief reviews); Claude only orchestrates, reviews, checks in a browser.

## The product (v3): a physics-based evidence engine for shadow-fleet monitoring
One engine, three theatres, one honest scoreboard:
1. **Global investigation (built):** GFW gaps → prism + Brownian bridge → radar at real scene time →
   sanctions → 5,624 → 109 → 19, with an evidence panel showing what radar can and can't prove.
2. **Baltic live engine (new core):** our own AIS recording (since Fri 23:18, ~45k msgs/h, full tracks +
   IMO) in Tiger Data → **kinematics**: own silences near shore (GFW misses them), impossible jumps
   (spoofing), one identity in two places (cloning), **ship-to-ship meetings seen in the tracks** (two
   vessels slow and close offshore), dark meetings (two silent ships whose prisms overlap) → **IMO
   watchlist** against OpenSanctions shadow-fleet/sanctions → live alerts + replay on the map.
3. **Edge unit (built; QNX if a Pi by 12:00):** camera sees a ship not broadcasting; AI killable live.
Scoreboard: every claim has a validation number from a test we pre-declared.

## Milestones and lanes
| Window | Milestone | Lanes (parallel, disjoint files) |
|---|---|---|
| Sat 03:45-09:00 (Kassem asleep) | **M4 Baltic engine** | L1 #72 tracks → Tiger (positions/static/coverage) · L2 #73 kinematics (silences, jumps, cloning) · L3 #75 STS/meeting detector from tracks · then L4 #74 ground-truth validation on masked tracks · L5 #76 IMO watchlist join |
| Sat 09:00 | **CP1** | Kassem: story (v3 pitch), Vultr, MLH codes, Pi hunt |
| Sat 09:00-15:00 | **M5 Live + online** | L6 #13 live alerts (SSE) + Baltic screen in console · L7 #15 deploy on Vultr (recorder + API + console, domain) · L8 #77 satellite quicklooks for candidates (Copernicus) · L9 #17 Entire in our workflow |
| Sat 15:00 | **CP2** | Opus Auditor + /code-review high + /security-review on everything; fix list |
| Sat 15:00-22:00 | **M6 Demo ready** | fixes from CP2 · replay mode (scrub last 24 h of Baltic) · edge on Pi (#36) if obtained · offline snapshot of all three theatres · demo script v1 + rehearsal |
| Sat 22:00 | **CP3** | Full offline rehearsal at 3:00; feature freeze |
| Sat 22:00-Sun 06:00 | **M7 Polish + story** | Critic/Director visual pass · README + architecture refresh · 60 s video · Devpost draft · DEV post draft (Comms review) · bug bash · buffer |
| Sun 06:00-08:30 | **M8 Submit** | final data refresh, snapshot, video, Devpost with tracks matching the live site, submit by 08:30 |
| Sun 10:00 / 11:30 | Judging | QNX sponsor table (if Pi) · main demo fair |

## Never cut
Validation numbers · global case file · Baltic kinematics with at least one real finding · offline snapshot ·
rehearsed 3:00 script. **Cut order if behind:** satellite quicklooks → Entire → live SSE (keep replay) →
edge on Pi → STS detector.

## Demo (3:00, v3 draft)
0:00 Baltic map, live: "We've recorded every ship in the Baltic since Friday night." · 0:20 a real silence
or impossible jump found in our own data, with the physics that proves it · 1:00 global funnel 5,624 → 109 →
19 and why 90 are innocent · 1:40 the evidence panel: what radar can't prove, what physics can (validation
numbers) · 2:20 watchlist hit (IMO match) or the OFAC name match labelled honestly · 2:45 who it's for.

## Gates (unchanged)
Issue → branch → PR → CI → Chief re-verify (real data or browser) → merge. Health + audit loop.
