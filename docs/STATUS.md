# STATUS (live)

Updated: Fri 20 Nov, ~11:45 PM ET · next checkpoint: CP1 briefing Sat 9 AM

## In flight
- Builders: GFW client + store (#4, #6), region scan F4 (#20), edge unit (#7).
- **Baltic AIS recorder running since Fri 23:18 ET** (`logs/record_baltic.log`, data in `data/cache/ais/`); must run until the demo. Laptop must stay awake and online.

## Done
- Scaffold (#1), research (#9), design (#8, Critic/Director pass pending), core prisms + matching (#2, #3; 18 tests).

## Next
- Own gap detection from recorded AIS (#19) once a few hours of data exist.
- M1: API + snapshot (#10), console (#11), scoring + sanctions (#12).

## Pivots tonight (evidence in docs/feasibility-dark-fleet.md)
- GFW publishes gaps only > 50 nm offshore → Baltic and Hormuz have ~0 gap events.
- GFW per-vessel presence API is down (404 "temporarily unavailable") → can't rebuild history.
- Plan: (1) Baltic = our own recorded AIS, our own gap detection, prisms, rendezvous, live;
  (2) satellite view = unmatched SAR in Hormuz; (3) prism+SAR case from wherever the region scan finds overlap.

## Blocked / waiting on Kassem
- Delete ~/Downloads/tiger-cloud-hack-western-credentials.env (plain-text password; copy now in .env).
- QNX Pi: ask around Saturday morning.
- MLH claim codes (Entire, GoDaddy): log in at mlh.com prizes page.

## Feasibility gates (docs/mission.md)
F1 GFW access: PASS · F2 SAR: PASS in Hormuz (0.01°, daily), Baltic empty · F3: Baltic FAIL, Hormuz PASS → region switched to Hormuz ·
F4 prism matcher: pending · F5 live AIS: Baltic PASS (591/min), Hormuz FAIL (2/90 s) → live layer = Baltic watchlist · F6 storage: PASS (Tiger Cloud, TimescaleDB 2.32) · F7 edge detector: pending
