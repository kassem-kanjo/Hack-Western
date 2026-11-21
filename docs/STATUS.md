# STATUS (live)

Updated: Sat 21 Nov, ~1:15 AM ET · next checkpoint: CP1 briefing Sat 9 AM · plan: docs/plan.md v2.1

## In flight
- #28 scene times + false-positive filters + re-rank (Haiku lead, DeepSeek coders, branch feat/scene-times).
- #27 validation: null test + recall (Sonnet lead, DeepSeek coders, worktree .worktrees/validation).
- #7 edge unit (Haiku lead; WIP backed up on branch wip/overnight).
- Baltic AIS recorder (since Fri 23:18 ET); health loop every 30 min (scripts/health.sh).

## Done (merged)
#1 scaffold · #2 #3 core prisms · #4 #6 data layer (Tiger Data: 6,049 SAR cells, 18 encounters) ·
#8 design · #9 research · #20 region scan (109 of 5,624 gaps with radar in prism) · #21 CI fix ·
plan v2.1 after Opus Auditor review · #29 API schema frozen.

## Key finding of the night
Auditor showed the planned opener (CL YICHUN) fails our own physics → validation (#27) is the headline.

## Blocked / waiting on Kassem
- Delete ~/Downloads/tiger-cloud-hack-western-credentials.env (plain-text password; copy now in .env).
- QNX Pi: ask around Saturday morning.
- MLH claim codes (Entire, GoDaddy): log in at mlh.com prizes page.

## Feasibility gates (docs/mission.md)
F1 GFW access: PASS · F2 SAR: PASS in Hormuz (0.01°, daily), Baltic empty · F3: Baltic FAIL, Hormuz PASS → region switched to Hormuz ·
F4 prism matcher: pending · F5 live AIS: Baltic PASS (591/min), Hormuz FAIL (2/90 s) → live layer = Baltic watchlist · F6 storage: PASS (Tiger Cloud, TimescaleDB 2.32) · F7 edge detector: pending
